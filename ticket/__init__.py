"""
SupportCog V5 - High-End Ticket System für RedBot
Features: 3-Button UI (Claim, Escalate, Close-Modal), Advanced Escalation, Auto-Close, HTML Transkript, Review System.
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

    @discord.ui.button(
        label='Ticket öffnen', 
        custom_id='support_ticket_create_btn', 
        style=discord.ButtonStyle.success, 
        emoji='🎫'
    )
    async def create_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = await self.cog.config.guild(interaction.guild).all()
        categories = config.get("categories", {})

        if not categories:
            await self.cog.create_ticket(interaction, "default", "Keine Beschreibung angegeben.")
            return

        options = []
        for cat_id, cat_data in categories.items():
            options.append(discord.SelectOption(
                label=cat_data["name"][:100],
                value=cat_id,
                description=cat_data.get("description", "")[:100],
                emoji=cat_data.get("emoji")
            ))

        view = discord.ui.View(timeout=180)
        select = discord.ui.Select(placeholder="Wähle eine Kategorie...", options=options, custom_id="ticket_cat_select")

        async def select_callback(inter: discord.Interaction):
            await inter.response.send_modal(TicketModal(self.cog, select.values[0]))

        select.callback = select_callback
        view.add_item(select)

        await interaction.response.send_message("Bitte wähle zuerst eine Kategorie für dein Ticket:", view=view, ephemeral=True)

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
        await self.cog.close_ticket(interaction, reason_str)

class TicketControlView(discord.ui.View):
    def __init__(self, cog: "SupportCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label='Übernehmen', 
        custom_id='support_ticket_claim_btn', 
        style=discord.ButtonStyle.success, 
        emoji='✋'
    )
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.claim_ticket(interaction, self)

    @discord.ui.button(
        label='Eskalieren', 
        custom_id='support_ticket_escalate_btn', 
        style=discord.ButtonStyle.secondary, 
        emoji='⚠️'
    )
    async def escalate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.escalate_ticket(interaction, self)

    @discord.ui.button(
        label='Schließen', 
        custom_id='support_ticket_close_btn', 
        style=discord.ButtonStyle.danger, 
        emoji='🔒'
    )
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CloseTicketModal(self.cog))

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
        self.panel_channel_id = None
        self.dm_notifications = True
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

        pan_sel = discord.ui.ChannelSelect(placeholder="Panel-Channel (wo das Panel gepostet wird)", channel_types=[discord.ChannelType.text], custom_id="base_panel")
        async def pan_cb(inter: discord.Interaction):
            self.panel_channel_id = pan_sel.values[0].id
            await inter.response.send_message("Panel-Channel aktualisiert.", ephemeral=True)
            self.update_ui()
            await self.message.edit(view=self)
        pan_sel.callback = pan_cb
        self.add_item(pan_sel)

        btn_dm = discord.ui.Button(label=f"DM-Benachrichtigungen: {'AN' if self.dm_notifications else 'AUS'}", style=discord.ButtonStyle.success if self.dm_notifications else discord.ButtonStyle.danger, emoji='✉️')
        async def dm_cb(inter: discord.Interaction):
            self.dm_notifications = not self.dm_notifications
            await inter.response.edit_message(view=self)
        btn_dm.callback = dm_cb
        self.add_item(btn_dm)

        btn_finish = discord.ui.Button(label="Setup abschließen & Panel erstellen", style=discord.ButtonStyle.primary, emoji='✅')
        async def finish_cb(inter: discord.Interaction):
            if not self.log_channel_id or not self.panel_channel_id:
                await inter.response.send_message("Bitte wähle zuerst beide Channels aus!", ephemeral=True)
                return
            await self.cog.finish_base_setup(inter, self)
            self.stop()
        btn_finish.callback = finish_cb
        self.add_item(btn_finish)

class CategorySetupView(discord.ui.View):
    def __init__(self, cog: "SupportCog", ctx: commands.Context):
        super().__init__(timeout=300)
        self.cog = cog
        self.ctx = ctx
        
        self.name = None
        self.description = None
        self.emoji = "🎫"
        self.abbr = "TICKET"
        self.button_color = "Blurple"
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
            await inter.response.send_modal(CategoryTextModal(self, "abbr", "Channel-Abkürzung", "z.B. SUP (für SUP-Max)", max_len=10))
        btn_abbr.callback = abbr_cb
        self.add_item(btn_abbr)

        btn_emoji = discord.ui.Button(label=f"Emoji: {self.emoji}", style=discord.ButtonStyle.secondary, custom_id="cat_emoji")
        async def emoji_cb(inter: discord.Interaction):
            await inter.response.send_modal(CategoryTextModal(self, "emoji", "Emoji", "Standard Emoji eingeben", max_len=10, required=False))
        btn_emoji.callback = emoji_cb
        self.add_item(btn_emoji)

        color_sel = discord.ui.Select(
            placeholder=f"Button Farbe: {self.button_color}",
            options=[discord.SelectOption(label=c, value=c) for c in ["Blurple", "Grün", "Rot", "Grau"]],
            custom_id="cat_color"
        )
        async def color_cb(inter: discord.Interaction):
            self.button_color = color_sel.values[0]
            self.update_ui()
            await inter.response.edit_message(view=self)
        color_sel.callback = color_cb
        self.add_item(color_sel)

        disc_cat_sel = discord.ui.ChannelSelect(
            placeholder=f"Discord Kategorie: {self.discord_category_id}" if self.discord_category_id else "Discord Kategorie wählen",
            channel_types=[discord.ChannelType.category],
            custom_id="cat_discord_cat"
        )
        async def disc_cat_cb(inter: discord.Interaction):
            self.discord_category_id = disc_cat_sel.values[0].id
            self.update_ui()
            await inter.response.edit_message(view=self)
        disc_cat_sel.callback = disc_cat_cb
        self.add_item(disc_cat_sel)

        staff_sel = discord.ui.RoleSelect(placeholder="Support-Rolle für diese Kategorie", custom_id="cat_staff_role")
        async def staff_cb(inter: discord.Interaction):
            self.staff_role_id = staff_sel.values[0].id
            await inter.response.send_message("Support-Rolle gesetzt.", ephemeral=True)
            self.update_ui()
            await self.message.edit(view=self)
        staff_sel.callback = staff_cb
        self.add_item(staff_sel)

        high_sel = discord.ui.RoleSelect(placeholder="High-Team Rolle (für Eskalation)", custom_id="cat_high_role")
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
                await inter.response.send_message("Bitte fülle mindestens Name, Abkürzung, Discord-Kategorie und Support-Rolle aus!", ephemeral=True)
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
        
        self.text_input = discord.ui.TextInput(
            label=title, placeholder=placeholder, required=required, max_length=max_len
        )
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
    """Ein hochmodernes, interaktives Ticket-System mit UI Components."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=98765432123456789, force_registration=True)
        
        default_guild = {
            "panel_channel_id": None,
            "panel_message_id": None,
            "log_channel_id": None,
            "dm_notifications": True,
            "categories": {},
            "active_tickets": [],
            "autoclose_hours": 48
        }
        
        self.config.register_guild(**default_guild)
        self.autoclose_task = None

    async def cog_load(self):
        # WICHTIG: Views und Tasks erst beim Laden registrieren, nicht im __init__
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

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        if not isinstance(channel, discord.TextChannel): return
        tickets = await self.config.guild(channel.guild).active_tickets()
        for t in tickets:
            if t["channel_id"] == channel.id:
                tickets.remove(t)
                await self.config.guild(channel.guild).active_tickets.set(tickets)
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
                        last_msg_str = t.get("last_message")
                        if not last_msg_str:
                            t["last_message"] = datetime.datetime.now().isoformat()
                            changed = True
                            continue
                        last_msg = datetime.datetime.fromisoformat(last_msg_str)
                        diff = datetime.datetime.now() - last_msg
                        if diff.total_seconds() > (autoclose_hours * 3600):
                            fake_inter = type("FakeInter", (), {"channel": channel, "guild": guild, "user": guild.me})()
                            await self.close_ticket(fake_inter, "Inaktivität (Auto-Close)", is_auto=True)
                            changed = True
                    if changed:
                        await self.config.guild(guild).active_tickets.set(tickets)
            except Exception as e:
                log.error(f"Fehler im Autoclose-Loop: {e}")
            await asyncio.sleep(300)

    @commands.group(name="ticket", aliases=["tickets"], invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def ticket_cmd(self, ctx: commands.Context):
        """Einstellungen für das Ticket-System."""
        await ctx.send_help(ctx.command)

    @ticket_cmd.command(name="setup")
    async def ticket_setup(self, ctx: commands.Context):
        """Startet den Basis-Setup-Wizard (Logs, Panel, DMs)."""
        view = BaseSetupView(self, ctx)
        embed = discord.Embed(title="🛠️ Ticket Basis-Setup", description="Konfiguriere die Grundlagen. Für die Kategorien nutze `[p]ticket addcat`.", color=discord.Color.blurple())
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @ticket_cmd.command(name="addcat")
    async def ticket_addcat(self, ctx: commands.Context):
        """Fügt eine neue Ticket-Kategorie hinzu (interaktiver Wizard)."""
        view = CategorySetupView(self, ctx)
        embed = discord.Embed(title="🏷️ Kategorie Setup", description="Konfiguriere alle Werte für diese Kategorie und klicke auf 'Speichern'.", color=discord.Color.green())
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @ticket_cmd.command(name="autoclose")
    async def ticket_autoclose(self, ctx: commands.Context, hours: int):
        """Setzt die Inaktivitätszeit in Stunden (0 = deaktiviert)."""
        await self.config.guild(ctx.guild).autoclose_hours.set(hours)
        await ctx.send(f"✅ Auto-Close auf {hours} Stunden gesetzt." if hours > 0 else "✅ Auto-Close deaktiviert.")

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
        if len(new_name) > 100: new_name = new_name[:100]
        await ctx.channel.edit(name=new_name)
        await ctx.send(f"✅ Ticket umbenannt in `{new_name}`.")

    # --- CORE LOGIC ---
    async def finish_base_setup(self, interaction: discord.Interaction, wizard: BaseSetupView):
        guild = interaction.guild
        await self.config.guild(guild).log_channel_id.set(wizard.log_channel_id)
        await self.config.guild(guild).panel_channel_id.set(wizard.panel_channel_id)
        await self.config.guild(guild).dm_notifications.set(wizard.dm_notifications)
        panel_channel = guild.get_channel(wizard.panel_channel_id)
        if not panel_channel: return await interaction.response.send_message("Panel Channel nicht gefunden!", ephemeral=True)
        
        embed = discord.Embed(
            title="🎫 Support Ticket System",
            description="Brauchst du Hilfe? Klicke auf **Ticket öffnen**.\n\n⚠️ **Wichtig:** Sobald ein Ticket geschlossen wird, wird der Chatverlauf als Transkript gespeichert.",
            color=discord.Color.blurple()
        )
        embed.set_footer(text=f"{guild.name} Support Team")
        view = TicketPanelView(self)
        panel_msg = await panel_channel.send(embed=embed, view=view)
        await self.config.guild(guild).panel_message_id.set(panel_msg.id)
        await interaction.response.edit_message(content=f"✅ Basis-Setup abgeschlossen! Panel gepostet in {panel_channel.mention}.", embed=None, view=None)

    async def save_category(self, interaction: discord.Interaction, wizard: CategorySetupView):
        guild = interaction.guild
        cat_id = str(uuid.uuid4())[:8]
        categories = await self.config.guild(guild).categories()
        categories[cat_id] = {
            "name": wizard.name,
            "description": wizard.description,
            "emoji": wizard.emoji,
            "abbr": wizard.abbr.upper(),
            "button_color": wizard.button_color,
            "discord_category_id": wizard.discord_category_id,
            "staff_role_id": wizard.staff_role_id,
            "high_team_role_id": wizard.high_team_role_id
        }
        await self.config.guild(guild).categories.set(categories)
        await interaction.response.edit_message(content=f"✅ Kategorie '{wizard.name}' gespeichert!", embed=None, view=None)

    async def create_ticket(self, interaction: discord.Interaction, cat_id: str, issue: str):
        guild = interaction.guild
        user = interaction.user
        config = await self.config.guild(guild).all()
        cat_data = config["categories"].get(cat_id)
        if not cat_data: return await interaction.response.send_message("❌ Kategorie existiert nicht mehr.", ephemeral=True)

        category = guild.get_channel(cat_data["discord_category_id"])
        if not category or not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message("❌ Fehler: Discord-Kategorie wurde gelöscht.", ephemeral=True)

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

        tickets = config["active_tickets"]
        tickets.append({"channel_id": ticket_channel.id, "user_id": user.id, "cat_id": cat_id, "last_message": datetime.datetime.now().isoformat(), "claimed_by": None, "escalated": False})
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
        await interaction.response.send_message(f"✅ Dein Ticket wurde erstellt: {ticket_channel.mention}", ephemeral=True)

    async def claim_ticket(self, interaction: discord.Interaction, view: TicketControlView):
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
            return await interaction.response.send_message("❌ Du bist nicht berechtigt, dieses Ticket zu übernehmen.", ephemeral=True)

        overwrites = channel.overwrites
        staff_role = guild.get_role(staff_role_id)

        if ticket_data["claimed_by"] is None:
            ticket_data["claimed_by"] = interaction.user.id
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)
            overwrites[interaction.user] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            
            for child in view.children:
                if child.custom_id == "support_ticket_claim_btn":
                    child.label = "Freigeben"
                    child.style = discord.ButtonStyle.secondary

            await interaction.response.edit_message(view=view)
            await channel.send(f"✅ {interaction.user.mention} hat das Ticket übernommen. Das Team kann nun nur noch mitlesen.")
        else:
            if ticket_data["claimed_by"] != interaction.user.id and not interaction.user.guild_permissions.manage_guild:
                return await interaction.response.send_message("❌ Nur die Person, die übernommen hat, kann das Ticket freigeben.", ephemeral=True)

            ticket_data["claimed_by"] = None
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            if interaction.user in overwrites:
                del overwrites[interaction.user]

            for child in view.children:
                if child.custom_id == "support_ticket_claim_btn":
                    child.label = "Übernehmen"
                    child.style = discord.ButtonStyle.success

            await interaction.response.edit_message(view=view)
            await channel.send("✅ Das Ticket wurde wieder freigegeben. Jeder Supporter kann sich nun darum kümmern.")

        await channel.edit(overwrites=overwrites)
        for i, t in enumerate(tickets):
            if t["channel_id"] == channel.id:
                tickets[i] = ticket_data
                break
        await self.config.guild(guild).active_tickets.set(tickets)

    async def escalate_ticket(self, interaction: discord.Interaction, view: TicketControlView):
        channel = interaction.channel
        guild = interaction.guild
        config = await self.config.guild(guild).all()
        ticket_data = next((t for t in config["active_tickets"] if t["channel_id"] == channel.id), None)
        if not ticket_data: return
        
        if ticket_data.get("escalated", False):
            return await interaction.response.send_message("❌ Dieses Ticket wurde bereits eskaliert.", ephemeral=True)

        cat_data = config["categories"].get(ticket_data["cat_id"], {})
        if not cat_data.get("high_team_role_id"):
            return await interaction.response.send_message("❌ Für diese Kategorie wurde kein High-Team konfiguriert.", ephemeral=True)

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
        await interaction.response.edit_message(view=view)

        ticket_data["escalated"] = True
        ticket_data["claimed_by"] = None
        await channel.send(f"⚠️ **Ticket eskaliert!** {interaction.user.mention} hat das High-Team ({high_role.mention}) hinzugezogen. Der Zugriff für das normale Team wurde entzogen.")

        for i, t in enumerate(config["active_tickets"]):
            if t["channel_id"] == channel.id:
                config["active_tickets"][i] = ticket_data
                break
        await self.config.guild(guild).active_tickets.set(config["active_tickets"])

    async def close_ticket(self, interaction: discord.Interaction, reason: str, is_auto: bool = False):
        channel = interaction.channel if hasattr(interaction, "channel") else interaction.channel
        guild = channel.guild
        config = await self.config.guild(guild).all()
        ticket_data = next((t for t in config["active_tickets"] if t["channel_id"] == channel.id), None)
        
        if not ticket_data:
            if hasattr(interaction, "response"): await interaction.response.send_message("❌ Dies ist kein aktives Ticket.", ephemeral=True)
            return

        if hasattr(interaction, "response"):
            await interaction.response.send_message("⏳ Ticket wird geschlossen und Transkript wird erstellt...", ephemeral=True)

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
        else: log_embed.add_field(name="Geschlossen von", value=f"{interaction.user.mention} (`{interaction.user.id}`)")
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

# DIESE FUNKTION IST ZWINGEND ERFORDERLICH FÜR REDBOT!
async def setup(bot):
    await bot.add_cog(SupportCog(bot))
