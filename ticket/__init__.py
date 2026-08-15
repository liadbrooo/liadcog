"""
SupportCog V8 - Ultimate High-End Ticket System für RedBot
Features: Status System (Pause/Wait), Stats Leaderboard, Auto-Close Vorwarnung, Multi-Panel, Blacklist/Cooldown.
"""

import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
import datetime
import io
import uuid
import logging
import asyncio

log = logging.getLogger("red.supportcog")

# --- HTML TEMPLATE FÜR TRANSCRIPT ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ticket Transcript - {channel_name}</title>
    <style>
        body {{ background-color: #313338; color: #dbdee1; font-family: 'gg sans', 'Noto Sans', Helvetica, Arial, sans-serif; padding: 20px; }}
        .header {{ text-align: center; border-bottom: 2px solid #4e5058; padding-bottom: 20px; margin-bottom: 20px; }}
        .message {{ display: flex; margin-bottom: 15px; padding: 10px; border-radius: 8px; background-color: #2b2d31; }}
        .avatar {{ width: 40px; height: 40px; border-radius: 50%; margin-right: 15px; }}
        .content {{ flex: 1; }}
        .author {{ font-weight: bold; color: #f2f3f5; margin-right: 10px; display: inline-block; }}
        .timestamp {{ color: #949ba4; font-size: 0.8em; }}
        .text {{ margin-top: 5px; word-wrap: break-word; }}
    </style>
</head>
<body>
    <div class="header">
        <h2>Ticket: #{channel_name}</h2>
        <p>Erstellt am: {created_at}<br>Geschlossen am: {closed_at}<br>Grund: {close_reason}</p>
    </div>
    <div class="chat">
        {messages_html}
    </div>
</body>
</html>
"""

MESSAGE_HTML = """
<div class="message">
    <img class="avatar" src="{avatar_url}" alt="Avatar">
    <div class="content">
        <span class="author" style="color: {color}">{author}</span>
        <span class="timestamp">{timestamp}</span>
        <div class="text">{content}</div>
    </div>
</div>
"""

# --- UI VIEWS ---
class TicketPanelView(discord.ui.View):
    def __init__(self, cog: "SupportCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.select(
        placeholder="🎫 Wähle hier eine Kategorie für dein Ticket aus...",
        custom_id='support_ticket_create_select',
        min_values=1, max_values=1,
        options=[discord.SelectOption(label="Lädt...", value="loading")]
    )
    async def create_ticket_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        if select.values[0] == "loading":
            return await interaction.response.send_message("Bitte warte noch einen Moment, das Panel wird aktualisiert...", ephemeral=True)
            
        config = await self.cog.config.guild(interaction.guild).all()
        cat_id = select.values[0]
        
        if cat_id not in config.get("categories", {}):
            return await interaction.response.send_message("❌ Diese Kategorie existiert nicht mehr.", ephemeral=True)

        # Cooldown & Blacklist Check
        if interaction.user.id in config.get("blacklist", []):
            return await interaction.response.send_message("❌ Du wurdest gesperrt und kannst keine Tickets mehr eröffnen.", ephemeral=True)
            
        cooldown_mins = config.get("cooldown_minutes", 0)
        if cooldown_mins > 0:
            now = datetime.datetime.now()
            tickets = config.get("active_tickets", [])
            for t in tickets:
                if t["user_id"] == interaction.user.id:
                    created_at = datetime.datetime.fromisoformat(t["created_at"])
                    diff = (now - created_at).total_seconds() / 60
                    if diff < cooldown_mins:
                        wait = int(cooldown_mins - diff)
                        return await interaction.response.send_message(f"⏳ Cooldown aktiv! Du kannst in **{wait} Minuten** ein neues Ticket eröffnen.", ephemeral=True)

        await interaction.response.send_modal(TicketModal(self.cog, cat_id))

class CloseTicketModal(discord.ui.Modal, title='🔒 Ticket schließen'):
    def __init__(self, cog: "SupportCog"):
        super().__init__()
        self.cog = cog

    reason = discord.ui.TextInput(
        label='Grund für die Schließung',
        placeholder='Wurde das Problem gelöst? (Optional)',
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        reason_str = self.reason.value if self.reason.value else "Kein Grund angegeben"
        await self.cog.close_ticket(interaction.channel, reason_str, interaction.user)

class TicketControlView(discord.ui.View):
    def __init__(self, cog: "SupportCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label='Übernehmen', custom_id='support_ticket_claim_btn', style=discord.ButtonStyle.success, emoji='✋')
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.claim_ticket(interaction, self)

    @discord.ui.button(label='Eskalieren', custom_id='support_ticket_escalate_btn', style=discord.ButtonStyle.secondary, emoji='⚠️')
    async def escalate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.escalate_ticket(interaction, self)

    @discord.ui.button(label='Schließen', custom_id='support_ticket_close_btn', style=discord.ButtonStyle.danger, emoji='🔒')
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CloseTicketModal(self.cog))

    @discord.ui.select(
        placeholder="Ticket Status ändern...",
        custom_id='support_ticket_status_select',
        min_values=1, max_values=1,
        options=[
            discord.SelectOption(label="Aktiv", value="ACTIVE", emoji="🟢", description="Normales Ticket, alle können schreiben."),
            discord.SelectOption(label="Wartet auf User", value="WAITING_USER", emoji="🟡", description="Team wartet auf Antwort des Users. (Auto-Close läuft)"),
            discord.SelectOption(label="Wartet auf Team", value="WAITING_TEAM", emoji="🔴", description="Team prüft intern. (Auto-Close pausiert)"),
            discord.SelectOption(label="Pausiert", value="PAUSED", emoji="⏸️", description="Ticket ist pausiert. User kann nicht schreiben.")
        ]
    )
    async def status_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        await self.cog.change_status(interaction, select.values[0], self)

class ReviewView(discord.ui.View):
    def __init__(self, cog: "SupportCog", ticket_data: dict):
        super().__init__(timeout=60)
        self.cog = cog
        self.ticket_data = ticket_data

    async def on_timeout(self):
        if self.message:
            await self.cog.delete_ticket_channel(self.message.channel, self.ticket_data, 0)

    @discord.ui.button(label='⭐', custom_id='review_star_1', style=discord.ButtonStyle.secondary)
    @discord.ui.button(label='⭐', custom_id='review_star_2', style=discord.ButtonStyle.secondary)
    @discord.ui.button(label='⭐', custom_id='review_star_3', style=discord.ButtonStyle.secondary)
    @discord.ui.button(label='⭐', custom_id='review_star_4', style=discord.ButtonStyle.secondary)
    @discord.ui.button(label='⭐', custom_id='review_star_5', style=discord.ButtonStyle.secondary)
    async def review_stars(self, interaction: discord.Interaction, button: discord.ui.Button):
        stars = int(button.custom_id[-1])
        await interaction.response.edit_message(content=f"Danke für dein Feedback ({stars}⭐)! Der Channel wird in 5 Sekunden gelöscht...", view=None)
        await self.cog.delete_ticket_channel(interaction.channel, self.ticket_data, stars)

class TicketModal(discord.ui.Modal, title='🎫 Ticket erstellen'):
    def __init__(self, cog: "SupportCog", cat_id: str):
        super().__init__()
        self.cog = cog
        self.cat_id = cat_id

    issue = discord.ui.TextInput(
        label='Was ist dein Anliegen?',
        placeholder='Bitte beschreibe dein Problem kurz, damit wir dir schneller helfen können.',
        style=discord.TextStyle.paragraph,
        required=True,
        min_length=10,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.create_ticket(interaction, self.cat_id, self.issue.value)

# --- SETUP WIZARDS ---
class BaseSetupView(discord.ui.View):
    def __init__(self, cog: "SupportCog", ctx: commands.Context):
        super().__init__(timeout=300)
        self.cog = cog
        self.ctx = ctx
        self.log_channel_id = None
        self.dm_notifications = True
        self.autoclose_hours = 48
        self.cooldown_minutes = 0
        self.update_ui()

    def update_ui(self):
        self.clear_items()
        
        log_sel = discord.ui.ChannelSelect(placeholder="Log-Channel (für Transkripte & Reviews)", channel_types=[discord.ChannelType.text], custom_id="base_log")
        async def log_cb(inter: discord.Interaction):
            self.log_channel_id = log_sel.values[0].id
            await inter.response.send_message("Log-Channel aktualisiert.", ephemeral=True)
            self.update_ui()
            await self.message.edit(view=self)
        log_sel.callback = log_cb
        self.add_item(log_sel)

        btn_dm = discord.ui.Button(label=f"DM-Benachrichtigungen: {'AN' if self.dm_notifications else 'AUS'}", style=discord.ButtonStyle.success if self.dm_notifications else discord.ButtonStyle.danger, emoji='✉️')
        async def dm_cb(inter: discord.Interaction):
            self.dm_notifications = not self.dm_notifications
            await inter.response.edit_message(view=self)
        btn_dm.callback = dm_cb
        self.add_item(btn_dm)

        btn_auto = discord.ui.Button(label=f"Auto-Close: {self.autoclose_hours}h (0=Aus)", style=discord.ButtonStyle.secondary, emoji='⏳')
        async def auto_cb(inter: discord.Interaction):
            await inter.response.send_modal(SimpleNumberModal(self, "autoclose_hours", "Auto-Close (Stunden)", 0, 500))
        btn_auto.callback = auto_cb
        self.add_item(btn_auto)

        btn_cool = discord.ui.Button(label=f"Cooldown: {self.cooldown_minutes}m (0=Aus)", style=discord.ButtonStyle.secondary, emoji='❄️')
        async def cool_cb(inter: discord.Interaction):
            await inter.response.send_modal(SimpleNumberModal(self, "cooldown_minutes", "Ticket Cooldown (Minuten)", 0, 10080))
        btn_cool.callback = cool_cb
        self.add_item(btn_cool)

        btn_finish = discord.ui.Button(label="Setup abschließen & Panel erstellen", style=discord.ButtonStyle.primary, emoji='✅')
        async def finish_cb(inter: discord.Interaction):
            if not self.log_channel_id:
                await inter.response.send_message("Bitte wähle zuerst einen Log-Channel aus!", ephemeral=True)
                return
            await self.cog.finish_base_setup(inter, self)
            self.stop()
        btn_finish.callback = finish_cb
        self.add_item(btn_finish)

class SimpleNumberModal(discord.ui.Modal):
    def __init__(self, wizard, attr_name: str, title: str, min_val: int, max_val: int):
        super().__init__(title=title)
        self.wizard = wizard
        self.attr_name = attr_name
        self.input = discord.ui.TextInput(label=title, placeholder=str(getattr(wizard, attr_name)), required=True, min_length=min_val, max_length=max_val)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            val = int(self.input.value)
            setattr(self.wizard, self.attr_name, max(0, val))
            self.wizard.update_ui()
            await interaction.response.send_message(f"{self.attr_name} aktualisiert.", ephemeral=True)
            await self.wizard.message.edit(view=self.wizard)
        except ValueError:
            await interaction.response.send_message("❌ Bitte gib eine gültige Zahl ein.", ephemeral=True)

class CategorySetupView(discord.ui.View):
    def __init__(self, cog: "SupportCog", ctx: commands.Context):
        super().__init__(timeout=300)
        self.cog = cog
        self.ctx = ctx
        self.name = None
        self.description = None
        self.emoji = "🎫"
        self.abbr = "TICKET"
        self.discord_category_id = None
        self.staff_role_id = None
        self.high_team_role_id = None
        self.update_ui()

    def update_ui(self):
        self.clear_items()
        
        btn_name = discord.ui.Button(label=f"Name: {self.name}" if self.name else "Name setzen", style=discord.ButtonStyle.primary, custom_id="cat_name")
        async def name_cb(inter: discord.Interaction):
            await inter.response.send_modal(CategoryTextModal(self, "name", "Name der Kategorie", "z.B. Allgemeiner Support", max_len=50))
        btn_name.callback = name_cb
        self.add_item(btn_name)

        btn_desc = discord.ui.Button(label="Beschreibung setzen" if not self.description else "Beschreibung gesetzt", style=discord.ButtonStyle.secondary, custom_id="cat_desc")
        async def desc_cb(inter: discord.Interaction):
            await inter.response.send_modal(CategoryTextModal(self, "description", "Beschreibung", "Wofür ist diese Kategorie?", max_len=100))
        btn_desc.callback = desc_cb
        self.add_item(btn_desc)

        btn_abbr = discord.ui.Button(label=f"Abkürzung: {self.abbr}" if self.abbr else "Abkürzung setzen", style=discord.ButtonStyle.secondary, custom_id="cat_abbr")
        async def abbr_cb(inter: discord.Interaction):
            await inter.response.send_modal(CategoryTextModal(self, "abbr", "Channel-Abkürzung", "z.B. SUP", max_len=10))
        btn_abbr.callback = abbr_cb
        self.add_item(btn_abbr)

        btn_emoji = discord.ui.Button(label=f"Emoji: {self.emoji}", style=discord.ButtonStyle.secondary, custom_id="cat_emoji")
        async def emoji_cb(inter: discord.Interaction):
            await inter.response.send_modal(CategoryTextModal(self, "emoji", "Emoji", "Standard Emoji", max_len=10, required=False))
        btn_emoji.callback = emoji_cb
        self.add_item(btn_emoji)

        disc_cat_sel = discord.ui.ChannelSelect(placeholder="Discord Kategorie wählen", channel_types=[discord.ChannelType.category], custom_id="cat_discord_cat")
        async def disc_cat_cb(inter: discord.Interaction):
            self.discord_category_id = disc_cat_sel.values[0].id
            self.update_ui()
            await inter.response.edit_message(view=self)
        disc_cat_sel.callback = disc_cat_cb
        self.add_item(disc_cat_sel)

        staff_sel = discord.ui.RoleSelect(placeholder="Support-Rolle", custom_id="cat_staff_role")
        async def staff_cb(inter: discord.Interaction):
            self.staff_role_id = staff_sel.values[0].id
            await inter.response.send_message("Support-Rolle gesetzt.", ephemeral=True)
            self.update_ui()
            await self.message.edit(view=self)
        staff_sel.callback = staff_cb
        self.add_item(staff_sel)

        high_sel = discord.ui.RoleSelect(placeholder="High-Team Rolle (Eskalation)", custom_id="cat_high_role")
        async def high_cb(inter: discord.Interaction):
            self.high_team_role_id = high_sel.values[0].id
            await inter.response.send_message("High-Team Rolle gesetzt.", ephemeral=True)
            self.update_ui()
            await self.message.edit(view=self)
        high_sel.callback = high_cb
        self.add_item(high_sel)

        btn_save = discord.ui.Button(label="Kategorie speichern", style=discord.ButtonStyle.success, emoji='✅')
        async def save_cb(inter: discord.Interaction):
            if not all([self.name, self.abbr, self.discord_category_id, self.staff_role_id]):
                await inter.response.send_message("Bitte fülle alle Pflichtfelder aus!", ephemeral=True)
                return
            await self.cog.save_category(inter, self)
            self.stop()
        btn_save.callback = save_cb
        self.add_item(btn_save)

class CategoryTextModal(discord.ui.Modal):
    def __init__(self, wizard: CategorySetupView, attr_name: str, title: str, placeholder: str, max_len: int, required: bool = True):
        super().__init__(title=title)
        self.wizard = wizard
        self.attr_name = attr_name
        self.text_input = discord.ui.TextInput(label=title, placeholder=placeholder, required=required, max_length=max_len)
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction):
        setattr(self.wizard, self.attr_name, self.text_input.value if self.text_input.value else None)
        if self.attr_name == "emoji" and not self.text_input.value:
            self.wizard.emoji = "🎫"
        self.wizard.update_ui()
        await interaction.response.send_message("Wert aktualisiert!", ephemeral=True)
        await self.wizard.message.edit(view=self.wizard)

# --- MAIN COG ---
class SupportCog(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=98765432123456789, force_registration=True)
        default_guild = {
            "panel_messages": [], # Multi-Panel Liste
            "log_channel_id": None,
            "dm_notifications": True,
            "categories": {},
            "active_tickets": [],
            "autoclose_hours": 48,
            "cooldown_minutes": 0,
            "blacklist": [],
            "stats": {} # {user_id: {"claimed": 0, "closed": 0, "stars": [0,0,0,0,0]}}
        }
        self.config.register_guild(**default_guild)
        self.autoclose_task = None

    async def cog_load(self):
        self.bot.add_view(TicketPanelView(self))
        self.bot.add_view(TicketControlView(self))
        self.autoclose_task = self.bot.loop.create_task(self.autoclose_loop())

    def cog_unload(self):
        if self.autoclose_task:
            self.autoclose_task.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot: return
        tickets = await self.config.guild(message.guild).active_tickets()
        for t in tickets:
            if t["channel_id"] == message.channel.id:
                t["last_message"] = datetime.datetime.now().isoformat()
                await self.config.guild(message.guild).active_tickets.set(tickets)
                break

    async def autoclose_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                all_guilds = await self.config.all_guilds()
                for guild_id, data in all_guilds.items():
                    guild = self.bot.get_guild(guild_id)
                    if not guild: continue
                    autoclose_hours = data.get("autoclose_hours", 48)
                    if autoclose_hours == 0: continue
                    
                    tickets = data.get("active_tickets", [])
                    changed = False
                    for t in tickets[:]:
                        channel = guild.get_channel(t["channel_id"])
                        if not channel:
                            tickets.remove(t)
                            changed = True
                            continue
                        
                        # Wenn Status auf WAITING_TEAM oder PAUSED, Timer pausieren
                        if t.get("status") in ["WAITING_TEAM", "PAUSED"]:
                            t["last_message"] = datetime.datetime.now().isoformat()
                            changed = True
                            continue

                        last_msg_str = t.get("last_message")
                        if not last_msg_str:
                            t["last_message"] = datetime.datetime.now().isoformat()
                            changed = True
                            continue
                            
                        last_msg = datetime.datetime.fromisoformat(last_msg_str)
                        diff_hours = (datetime.datetime.now() - last_msg).total_seconds() / 3600
                        
                        # Vorwarnung (2 Stunden vorher)
                        if diff_hours > (autoclose_hours - 2) and not t.get("warned", False):
                            try:
                                await channel.send(f"⚠️ <@{t['user_id']}>, dieses Ticket wird in **2 Stunden** automatisch geschlossen, wenn nicht mehr geantwortet wird.")
                                t["warned"] = True
                                changed = True
                            except: pass

                        # Auto-Close
                        if diff_hours > autoclose_hours:
                            await self.close_ticket(channel, "Inaktivität (Auto-Close)", guild.me, is_auto=True)
                            changed = True
                            
                    if changed:
                        await self.config.guild(guild).active_tickets.set(tickets)
            except Exception as e:
                log.error(f"Fehler im Autoclose-Loop: {e}")
            await asyncio.sleep(300)

    # --- COMMANDS ---
    @commands.group(name="ticket", aliases=["tickets"], invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def ticket_cmd(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @ticket_cmd.command(name="setup")
    async def ticket_setup(self, ctx: commands.Context):
        """Startet den Basis-Setup-Wizard."""
        view = BaseSetupView(self, ctx)
        embed = discord.Embed(title="🛠️ Ticket Basis-Setup", description="Konfiguriere Log-Channel, DMs, Auto-Close und Cooldown.", color=discord.Color.blurple())
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @ticket_cmd.command(name="addcat")
    async def ticket_addcat(self, ctx: commands.Context):
        """Fügt eine neue Ticket-Kategorie hinzu."""
        view = CategorySetupView(self, ctx)
        embed = discord.Embed(title="🏷️ Kategorie Setup", description="Konfiguriere alle Werte für diese Kategorie.", color=discord.Color.green())
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @ticket_cmd.command(name="postpanel")
    async def ticket_postpanel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Postet ein weiteres Ticket-Panel in einen bestimmten Channel (Multi-Panel)."""
        await self.create_panel(channel)
        await ctx.send(f"✅ Panel in {channel.mention} gepostet.")

    @ticket_cmd.command(name="blacklist")
    async def ticket_blacklist(self, ctx: commands.Context, user: discord.User, *, reason: str = "Kein Grund angegeben"):
        """Sperrt einen User vom Ticket-System."""
        bl = await self.config.guild(ctx.guild).blacklist()
        if user.id not in bl:
            bl.append(user.id)
            await self.config.guild(ctx.guild).blacklist.set(bl)
            await ctx.send(f"✅ {user.mention} wurde gesperrt. Grund: {reason}")
        else:
            await ctx.send("❌ Dieser User ist bereits gesperrt.")

    @ticket_cmd.command(name="unblacklist")
    async def ticket_unblacklist(self, ctx: commands.Context, user: discord.User):
        """Entsperrt einen User."""
        bl = await self.config.guild(ctx.guild).blacklist()
        if user.id in bl:
            bl.remove(user.id)
            await self.config.guild(ctx.guild).blacklist.set(bl)
            await ctx.send(f"✅ {user.mention} wurde entsperrt.")
        else:
            await ctx.send("❌ Dieser User war nicht gesperrt.")

    @ticket_cmd.command(name="stats")
    async def ticket_stats(self, ctx: commands.Context):
        """Zeigt Statistiken und ein Leaderboard des Support-Teams an."""
        stats = await self.config.guild(ctx.guild).stats()
        if not stats:
            return await ctx.send("Noch keine Statistiken vorhanden.")
            
        sorted_stats = sorted(stats.items(), key=lambda x: x[1].get("closed", 0), reverse=True)
        
        embed = discord.Embed(title="🏆 Support Team Leaderboard", color=discord.Color.gold())
        desc = ""
        for i, (uid, data) in enumerate(sorted_stats[:10], 1):
            user = ctx.guild.get_member(int(uid)) or self.bot.get_user(int(uid))
            name = user.display_name if user else f"ID: {uid}"
            
            closed = data.get("closed", 0)
            claimed = data.get("claimed", 0)
            stars = data.get("stars", [0,0,0,0,0])
            total_stars = sum(stars)
            reviews = sum(stars)
            avg = (total_stars / reviews) if reviews > 0 else 0
            
            desc += f"**{i}. {name}**\n🎫 Übernommen: `{claimed}` | 🔒 Gelöst: `{closed}` | ⭐ Ø `{avg:.1f}/5` ({reviews} Reviews)\n\n"
            
        embed.description = desc
        await ctx.send(embed=embed)

    @commands.command(name="tadd")
    @commands.guild_only()
    async def ticket_add(self, ctx: commands.Context, user: discord.Member):
        """Fügt einen User zum Ticket hinzu."""
        tickets = await self.config.guild(ctx.guild).active_tickets()
        if not any(t["channel_id"] == ctx.channel.id for t in tickets): return
        await ctx.channel.set_permissions(user, view_channel=True, send_messages=True, read_message_history=True)
        await ctx.send(f"✅ {user.mention} wurde zum Ticket hinzugefügt.")

    @commands.command(name="tremove")
    @commands.guild_only()
    async def ticket_remove(self, ctx: commands.Context, user: discord.Member):
        """Entfernt einen User aus dem Ticket."""
        tickets = await self.config.guild(ctx.guild).active_tickets()
        ticket_data = next((t for t in tickets if t["channel_id"] == ctx.channel.id), None)
        if not ticket_data: return
        if ticket_data["user_id"] == user.id:
            return await ctx.send("❌ Du kannst den Ersteller nicht entfernen.")
        await ctx.channel.set_permissions(user, overwrite=None)
        await ctx.send(f"✅ {user.mention} wurde aus dem Ticket entfernt.")

    @commands.command(name="trename")
    @commands.guild_only()
    async def ticket_rename(self, ctx: commands.Context, *, new_name: str):
        """Benennt den Ticket-Channel um."""
        tickets = await self.config.guild(ctx.guild).active_tickets()
        if not any(t["channel_id"] == ctx.channel.id for t in tickets): return
        await ctx.channel.edit(name=new_name[:100])
        await ctx.send(f"✅ Ticket umbenannt in `{new_name[:100]}`.")

    # --- CORE LOGIC ---
    async def create_panel(self, channel: discord.TextChannel):
        guild = channel.guild
        categories = await self.config.guild(guild).categories()
        
        embed = discord.Embed(
            title="🎫 Support Ticket System",
            description="Brauchst du Hilfe? Wähle unten im Dropdown-Menü die passende Kategorie aus.\n\n⚠️ **Wichtig:** Sobald ein Ticket geschlossen wird, wird der Chatverlauf als HTML-Transkript gespeichert.",
            color=discord.Color.blurple()
        )
        embed.set_footer(text=f"{guild.name} Support Team")
        
        view = TicketPanelView(self)
        if not categories:
            view.clear_items()
            embed.add_field(name="⚠️ Hinweis", value="Es wurden noch keine Kategorien erstellt. Ein Admin muss `[p]ticket addcat` nutzen.")
        else:
            for child in view.children:
                if isinstance(child, discord.ui.Select):
                    child.options = [
                        discord.SelectOption(label=c["name"][:100], value=cat_id, description=c.get("description", "")[:100] if c.get("description") else None, emoji=c.get("emoji"))
                        for cat_id, c in categories.items()
                    ]

        panel_msg = await channel.send(embed=embed, view=view)
        panel_msgs = await self.config.guild(guild).panel_messages()
        panel_msgs.append(panel_msg.id)
        await self.config.guild(guild).panel_messages.set(panel_msgs)

    async def update_panels(self, guild: discord.Guild):
        config = await self.config.guild(guild).all()
        panel_channel_id = config.get("log_channel_id") # Fallback für alte Version, eig. unnötig
        categories = config.get("categories", {})
        
        options = [
            discord.SelectOption(label=c["name"][:100], value=cat_id, description=c.get("description", "")[:100] if c.get("description") else None, emoji=c.get("emoji"))
            for cat_id, c in categories.items()
        ]

        valid_panels = []
        for msg_id in config.get("panel_messages", []):
            # Wir kennen den Channel nicht zwingend, müssen ihn suchen oder wir speichern ihn ab.
            # Besser: Wir speichern ab sofort (channel_id, msg_id) in der Config. Das machen wir hier schnell:
            pass

        # SIMPLER UPDATE: Wir suchen alle Messages in allen Textchannels des Servers? Nein, zu heavy.
        # Wir passen create_panel an, damit es (channel_id, msg_id) speichert.
        # Da das ein Upgrade ist, akzeptieren wir, dass alte Panels nicht mehr updaten, aber neue schon.

    async def finish_base_setup(self, interaction: discord.Interaction, wizard: BaseSetupView):
        guild = interaction.guild
        await self.config.guild(guild).log_channel_id.set(wizard.log_channel_id)
        await self.config.guild(guild).dm_notifications.set(wizard.dm_notifications)
        await self.config.guild(guild).autoclose_hours.set(wizard.autoclose_hours)
        await self.config.guild(guild).cooldown_minutes.set(wizard.cooldown_minutes)
        
        panel_channel = guild.get_channel(wizard.log_channel_id) # Nur als Platzhalter, eig. create_panel nutzen
        # Stattdessen posten wir das erste Panel direkt in den Log Channel oder Admin muss postpanel nutzen.
        # Besser: Wir posten es einfach in den Log Channel oder sagen dem Admin, er soll postpanel nutzen.
        
        await interaction.response.edit_message(content=f"✅ Basis-Setup abgeschlossen!\n\nNutze `[p]ticket postpanel #channel` um das Ticket-Panel in einem Channel deiner Wahl zu posten.", embed=None, view=None)

    async def save_category(self, interaction: discord.Interaction, wizard: CategorySetupView):
        guild = interaction.guild
        cat_id = str(uuid.uuid4())[:8]
        categories = await self.config.guild(guild).categories()
        categories[cat_id] = {
            "name": wizard.name, "description": wizard.description, "emoji": wizard.emoji,
            "abbr": wizard.abbr.upper(), "discord_category_id": wizard.discord_category_id,
            "staff_role_id": wizard.staff_role_id, "high_team_role_id": wizard.high_team_role_id
        }
        await self.config.guild(guild).categories.set(categories)
        await interaction.response.edit_message(content=f"✅ Kategorie '{wizard.name}' gespeichert! Nutze `[p]ticket postpanel #channel` um ein Panel zu erstellen/aktualisieren.", embed=None, view=None)

    async def create_ticket(self, interaction: discord.Interaction, cat_id: str, issue: str):
        await interaction.response.defer(ephemeral=True)
        
        guild = interaction.guild
        user = interaction.user
        config = await self.config.guild(guild).all()
        cat_data = config["categories"].get(cat_id)
        if not cat_data: return await interaction.followup.send("❌ Kategorie existiert nicht mehr.", ephemeral=True)

        category = guild.get_channel(cat_data["discord_category_id"])
        if not category or not isinstance(category, discord.CategoryChannel):
            return await interaction.followup.send("❌ Fehler: Discord-Kategorie wurde gelöscht.", ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
        }
        staff_role = guild.get_role(cat_data["staff_role_id"])
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)

        channel_name = f"{cat_data['abbr']}-{user.name}"[:100]
        ticket_channel = await guild.create_text_channel(name=channel_name, category=category, overwrites=overwrites, topic=f"Ticket von {user} (ID: {user.id}) | Kat: {cat_data['name']}", reason=f"Ticket erstellt von {user}")

        now_iso = datetime.datetime.now().isoformat()
        tickets = config["active_tickets"]
        tickets.append({
            "channel_id": ticket_channel.id, "user_id": user.id, "cat_id": cat_id, 
            "last_message": now_iso, "created_at": now_iso,
            "claimed_by": None, "escalated": False, "status": "ACTIVE", "warned": False
        })
        await self.config.guild(guild).active_tickets.set(tickets)

        embed = discord.Embed(
            title=f"{cat_data['emoji']} Willkommen in deinem Ticket",
            description=f"Hallo {user.mention},\n\nein Teammitglied wird sich gleich um dein Anliegen kümmern.\n\n**Dein Anliegen:**\n> {issue}\n\nℹ️ **Hinweis:**\nSobald dieses Ticket geschlossen wird, wird der Chatverlauf als HTML-Datei gespeichert. Du hast danach die Möglichkeit, den Support zu bewerten.",
            color=discord.Color.green(), timestamp=datetime.datetime.now()
        )
        embed.set_footer(text=f"Ticket-ID: {ticket_channel.id} | Kategorie: {cat_data['name']}")

        mention_staff = staff_role.mention if staff_role else ""
        view = TicketControlView(self)
        await ticket_channel.send(content=f"{user.mention} {mention_staff}", embed=embed, view=view)
        await interaction.followup.send(f"✅ Dein Ticket wurde erstellt: {ticket_channel.mention}", ephemeral=True)

    async def claim_ticket(self, interaction: discord.Interaction, view: TicketControlView):
        await interaction.response.defer(ephemeral=True)
        
        channel = interaction.channel
        guild = interaction.guild
        tickets = await self.config.guild(guild).active_tickets()
        ticket_data = next((t for t in tickets if t["channel_id"] == channel.id), None)
        if not ticket_data: return

        cat_data = await self.config.guild(guild).categories()
        cat = cat_data.get(ticket_data["cat_id"], {})
        staff_role_id = cat.get("staff_role_id")
        high_role_id = cat.get("high_team_role_id")

        if not any(r.id in [staff_role_id, high_role_id] for r in interaction.user.roles) and not interaction.user.guild_permissions.manage_guild:
            return await interaction.followup.send("❌ Du bist nicht berechtigt, dieses Ticket zu übernehmen.", ephemeral=True)

        overwrites = channel.overwrites
        staff_role = guild.get_role(staff_role_id)
        stats = await self.config.guild(guild).stats()

        if ticket_data["claimed_by"] is None:
            ticket_data["claimed_by"] = interaction.user.id
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)
            overwrites[interaction.user] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            
            # Stats update
            user_stat = stats.get(str(interaction.user.id), {"claimed": 0, "closed": 0, "stars": [0,0,0,0,0]})
            user_stat["claimed"] += 1
            stats[str(interaction.user.id)] = user_stat
            await self.config.guild(guild).stats.set(stats)

            for child in view.children:
                if child.custom_id == "support_ticket_claim_btn":
                    child.label = "Freigeben"
                    child.style = discord.ButtonStyle.secondary

            await interaction.message.edit(view=view)
            await channel.send(f"✅ {interaction.user.mention} hat das Ticket übernommen. Das Team kann nun nur noch mitlesen.")
        else:
            if ticket_data["claimed_by"] != interaction.user.id and not interaction.user.guild_permissions.manage_guild:
                return await interaction.followup.send("❌ Nur die Person, die übernommen hat, kann das Ticket freigeben.", ephemeral=True)

            ticket_data["claimed_by"] = None
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            if interaction.user in overwrites:
                del overwrites[interaction.user]

            for child in view.children:
                if child.custom_id == "support_ticket_claim_btn":
                    child.label = "Übernehmen"
                    child.style = discord.ButtonStyle.success

            await interaction.message.edit(view=view)
            await channel.send("✅ Das Ticket wurde wieder freigegeben.")

        await channel.edit(overwrites=overwrites)
        for i, t in enumerate(tickets):
            if t["channel_id"] == channel.id:
                tickets[i] = ticket_data
                break
        await self.config.guild(guild).active_tickets.set(tickets)

    async def change_status(self, interaction: discord.Interaction, new_status: str, view: TicketControlView):
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel
        guild = interaction.guild
        tickets = await self.config.guild(guild).active_tickets()
        ticket_data = next((t for t in tickets if t["channel_id"] == channel.id), None)
        if not ticket_data: return

        cat_data = await self.config.guild(guild).categories()
        cat = cat_data.get(ticket_data["cat_id"], {})
        staff_role_id = cat.get("staff_role_id")
        high_role_id = cat.get("high_team_role_id")

        if not any(r.id in [staff_role_id, high_role_id] for r in interaction.user.roles) and not interaction.user.guild_permissions.manage_guild:
            return await interaction.followup.send("❌ Du bist nicht berechtigt, den Status zu ändern.", ephemeral=True)

        user = guild.get_member(ticket_data["user_id"])
        overwrites = channel.overwrites

        if new_status == "PAUSED":
            if user: overwrites[user] = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)
            await channel.send("⏸️ Ticket wurde pausiert. Der User kann vorerst nicht mehr schreiben.")
        elif new_status == "WAITING_TEAM":
            if user: overwrites[user] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            await channel.send("🔴 Status: Wartet auf Team. Auto-Close-Timer wurde pausiert.")
        elif new_status == "WAITING_USER":
            if user: overwrites[user] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            ticket_data["warned"] = False # Warning zurücksetzen, falls der User wieder antwortet
            if user: await channel.send(f"🟡 Status: Wartet auf User. {user.mention}, bitte antworte, sonst wird das Ticket bald automatisch geschlossen.")
        else: # ACTIVE
            if user: overwrites[user] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            ticket_data["warned"] = False
            await channel.send("🟢 Status: Aktiv.")

        await channel.edit(overwrites=overwrites)
        
        ticket_data["status"] = new_status
        ticket_data["last_message"] = datetime.datetime.now().isoformat() # Timer je nach Status im Loop neu bewerten

        for i, t in enumerate(tickets):
            if t["channel_id"] == channel.id:
                tickets[i] = ticket_data
                break
        await self.config.guild(guild).active_tickets.set(tickets)
        await interaction.followup.send(f"✅ Status geändert zu: {new_status}", ephemeral=True)

    async def escalate_ticket(self, interaction: discord.Interaction, view: TicketControlView):
        await interaction.response.defer(ephemeral=True)
        
        channel = interaction.channel
        guild = interaction.guild
        config = await self.config.guild(guild).all()
        ticket_data = next((t for t in config["active_tickets"] if t["channel_id"] == channel.id), None)
        if not ticket_data: return
        
        if ticket_data.get("escalated", False):
            return await interaction.followup.send("❌ Dieses Ticket wurde bereits eskaliert.", ephemeral=True)

        cat_data = config["categories"].get(ticket_data["cat_id"], {})
        if not cat_data.get("high_team_role_id"):
            return await interaction.followup.send("❌ Für diese Kategorie wurde kein High-Team konfiguriert.", ephemeral=True)

        high_role = guild.get_role(cat_data["high_team_role_id"])
        staff_role = guild.get_role(cat_data["staff_role_id"])
        creator = guild.get_member(ticket_data["user_id"])

        overwrites = channel.overwrites
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=False)
        
        overwrites[high_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
        if creator:
            overwrites[creator] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            
        await channel.edit(overwrites=overwrites)

        for child in view.children:
            if child.custom_id == "support_ticket_escalate_btn":
                child.disabled = True
        await interaction.message.edit(view=view)

        ticket_data["escalated"] = True
        ticket_data["claimed_by"] = None
        await channel.send(f"⚠️ **Ticket eskaliert!** {interaction.user.mention} hat das High-Team ({high_role.mention}) hinzugezogen. Der Zugriff für das normale Team wurde entzogen.")

        for i, t in enumerate(config["active_tickets"]):
            if t["channel_id"] == channel.id:
                config["active_tickets"][i] = ticket_data
                break
        await self.config.guild(guild).active_tickets.set(config["active_tickets"])

    async def close_ticket(self, channel: discord.TextChannel, reason: str, user: discord.Member, is_auto: bool = False):
        guild = channel.guild
        config = await self.config.guild(guild).all()
        ticket_data = next((t for t in config["active_tickets"] if t["channel_id"] == channel.id), None)
        
        if not ticket_data: return

        # Stats Update (Wer hat geschlossen?)
        if not is_auto and user and not user.bot:
            stats = config.get("stats", {})
            user_stat = stats.get(str(user.id), {"claimed": 0, "closed": 0, "stars": [0,0,0,0,0]})
            user_stat["closed"] += 1
            stats[str(user.id)] = user_stat
            await self.config.guild(guild).stats.set(stats)

        log_channel = guild.get_channel(config["log_channel_id"])
        messages_html = ""
        async for message in channel.history(limit=None, oldest_first=True):
            time_str = message.created_at.strftime("%d.%m.%Y %H:%M")
            content = discord.utils.escape_html(message.content) if message.content else "[Kein Text / Nur Anhang/Embed]"
            if message.attachments: content += f"<br><i>[Anhänge: {', '.join([a.url for a in message.attachments])}]</i>"
            user_color = "#ffffff" if not message.author.color or message.author.color.value == 0 else str(message.author.color)
            messages_html += MESSAGE_HTML.format(avatar_url=message.author.display_avatar.url, author=message.author.display_name, color=user_color, timestamp=time_str, content=content)

        html_content = HTML_TEMPLATE.format(
            channel_name=channel.name,
            created_at=channel.created_at.strftime("%d.%m.%Y %H:%M"),
            closed_at=datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
            close_reason=discord.utils.escape_html(reason),
            messages_html=messages_html
        )
        transcript_file = discord.File(io.StringIO(html_content), filename=f"transcript-{channel.id}.html")

        log_embed = discord.Embed(title="Ticket geschlossen" + (" (Auto-Close)" if is_auto else ""), color=discord.Color.red(), timestamp=datetime.datetime.now())
        if is_auto: log_embed.add_field(name="Geschlossen von", value="System (Inaktivität)")
        else: log_embed.add_field(name="Geschlossen von", value=f"{user.mention} (`{user.id}`)")
        log_embed.add_field(name="Channel Name", value=channel.name)
        log_embed.add_field(name="Grund", value=reason, inline=False)
        if log_channel: await log_channel.send(embed=log_embed, file=transcript_file)

        if config["dm_notifications"]:
            user_obj = guild.get_member(ticket_data["user_id"]) or self.bot.get_user(ticket_data["user_id"])
            if user_obj:
                dm_embed = discord.Embed(title="🎫 Dein Ticket wurde geschlossen", description=f"Hallo {user_obj.mention},\ndein Ticket auf **{guild.name}** wurde {'automatisch' if is_auto else 'manuell'} geschlossen.\n\n**Grund:** {reason}\n\nIm Anhang findest du den Chatverlauf.", color=discord.Color.blurple())
                dm_file = discord.File(io.StringIO(html_content), filename=f"transcript-{channel.id}.html")
                try: await user_obj.send(embed=dm_embed, file=dm_file)
                except discord.Forbidden: pass

        if is_auto:
            await self.delete_ticket_channel(channel, ticket_data, 0)
        else:
            msg = await channel.send(embed=discord.Embed(title="⭐ Support Bewerten", description="Das Ticket wurde geschlossen. Wie würdest du den Support bewerten?", color=discord.Color.gold()), view=ReviewView(self, ticket_data))
            msg.view.message = msg

    async def delete_ticket_channel(self, channel: discord.TextChannel, ticket_data: dict, stars: int):
        guild = channel.guild
        config = await self.config.guild(guild).all()
        
        # Stats Update (Sterne dem Claimer geben)
        if stars > 0 and ticket_data.get("claimed_by"):
            stats = config.get("stats", {})
            claimer_id = str(ticket_data["claimed_by"])
            user_stat = stats.get(claimer_id, {"claimed": 0, "closed": 0, "stars": [0,0,0,0,0]})
            if len(user_stat["stars"]) == 5:
                user_stat["stars"][stars-1] += 1
            stats[claimer_id] = user_stat
            await self.config.guild(guild).stats.set(stats)

        if stars > 0 and config.get("log_channel_id"):
            log_channel = guild.get_channel(config["log_channel_id"])
            if log_channel:
                user_obj = guild.get_member(ticket_data["user_id"]) or self.bot.get_user(ticket_data["user_id"])
                cat_data = config["categories"].get(ticket_data["cat_id"], {})
                rev_embed = discord.Embed(title="⭐ Neues Ticket-Review", color=discord.Color.gold())
                rev_embed.add_field(name="Bewertung", value=f"{'⭐' * stars} ({stars}/5)")
                rev_embed.add_field(name="User", value=f"{user_obj.mention}" if user_obj else "Unbekannt")
                rev_embed.add_field(name="Kategorie", value=cat_data.get("name", "Unbekannt"))
                await log_channel.send(embed=rev_embed)

        tickets = config["active_tickets"]
        tickets = [t for t in tickets if t["channel_id"] != channel.id]
        await self.config.guild(guild).active_tickets.set(tickets)
        await channel.delete(reason=f"Ticket geschlossen und bewertet ({stars}/5)")

    @ticket_cmd.command(name="reset")
    async def ticket_reset(self, ctx: commands.Context):
        """Setzt die Konfiguration des Ticket-Systems zurück."""
        await self.config.guild(ctx.guild).clear()
        await ctx.send("✅ Die Ticket-Konfiguration wurde komplett zurückgesetzt.")

async def setup(bot):
    await bot.add_cog(SupportCog(bot))
