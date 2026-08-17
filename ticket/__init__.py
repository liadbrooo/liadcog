import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
import datetime
import uuid
import logging
import asyncio

log = logging.getLogger("red.supportcog")

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

class TicketPanelView(discord.ui.View):
    def __init__(self, cog: "SupportCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.select(
        placeholder="🎫 Wähle hier eine Kategorie für dein Ticket aus...",
        custom_id='support_ticket_create_select',
        min_values=1,
        max_values=1,
        options=[discord.SelectOption(label="Lädt...", value="loading")]
    )
    async def create_ticket_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        if select.values[0] == "loading":
            return await interaction.response.send_message(
                "Bitte warte noch einen Moment, das Panel wird aktualisiert...",
                ephemeral=True
            )

        config = await self.cog.config.guild(interaction.guild).all()
        cat_id = select.values[0]
        if cat_id not in config.get("categories", {}):
            return await interaction.response.send_message(
                "❌ Diese Kategorie existiert nicht mehr.",
                ephemeral=True
            )

        if interaction.user.id in config.get("blacklist", []):
            return await interaction.response.send_message(
                "❌ Du wurdest gesperrt und kannst keine Tickets mehr eröffnen.",
                ephemeral=True
            )

        max_tickets = config.get("max_tickets_per_user", 1)
        user_tickets = [t for t in config.get("active_tickets", []) if t["user_id"] == interaction.user.id]
        if len(user_tickets) >= max_tickets:
            return await interaction.response.send_message(
                f"❌ Du hast bereits das Maximum von **{max_tickets}** offenen Tickets. Bitte schließe zuerst eines.",
                ephemeral=True
            )

        cooldown_mins = config.get("cooldown_minutes", 0)
        if cooldown_mins > 0:
            now = datetime.datetime.now()
            for t in user_tickets:
                diff = (now - datetime.datetime.fromisoformat(t["created_at"])).total_seconds() / 60
                if diff < cooldown_mins:
                    return await interaction.response.send_message(
                        f"⏳ Cooldown aktiv! Du kannst in **{int(cooldown_mins - diff)} Minuten** ein neues Ticket eröffnen.",
                        ephemeral=True
                    )

        cat_data = config["categories"].get(cat_id, {})
        cat_max_tickets = cat_data.get("max_tickets", 10)
        if cat_max_tickets > 0:
            active_count = sum(
                1 for t in config["active_tickets"]
                if t["cat_id"] == cat_id and t.get("status") == "ACTIVE"
            )
            if active_count >= cat_max_tickets:
                return await interaction.response.send_message(
                    "❌ Diese Kategorie ist aktuell ausgelastet. Bitte warte einen Moment.",
                    ephemeral=True
                )

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
        await interaction.response.defer(ephemeral=True)
        await self.cog.close_ticket(
            interaction.channel,
            self.reason.value or "Kein Grund angegeben",
            interaction.user,
            interaction=interaction
        )

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
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="Aktiv", value="ACTIVE", emoji="🟢", description="Normales Ticket."),
            discord.SelectOption(label="Wartet auf User", value="WAITING_USER", emoji="🟡", description="Team wartet auf Antwort."),
            discord.SelectOption(label="Wartet auf Team", value="WAITING_TEAM", emoji="🔴", description="Team prüft intern."),
            discord.SelectOption(label="Pausiert", value="PAUSED", emoji="⏸️", description="Ticket ist pausiert.")
        ]
    )
    async def status_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        await self.cog.change_status(interaction, select.values[0], self)

class TicketModal(discord.ui.Modal, title='🎫 Ticket erstellen'):
    def __init__(self, cog: "SupportCog", cat_id: str):
        super().__init__()
        self.cog = cog
        self.cat_id = cat_id

    issue = discord.ui.TextInput(
        label='Was ist dein Anliegen?',
        placeholder='Bitte beschreibe dein Problem kurz...',
        style=discord.TextStyle.paragraph,
        required=True,
        min_length=10,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.create_ticket(interaction, self.cat_id, self.issue.value)

class SimpleNumberModal(discord.ui.Modal):
    def __init__(self, wizard, attr_name: str, title: str, min_val: int, max_val: int):
        super().__init__(title=title)
        self.wizard = wizard
        self.attr_name = attr_name
        self.min_val = min_val
        self.max_val = max_val
        self.input = discord.ui.TextInput(
            label=title,
            placeholder=str(getattr(wizard, attr_name)),
            required=True,
            min_length=1,
            max_length=10
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            val = int(self.input.value)
        except ValueError:
            await interaction.response.send_message("❌ Bitte gib eine gültige Zahl ein.", ephemeral=True)
            return

        if not (self.min_val <= val <= self.max_val):
            await interaction.response.send_message(
                f"❌ Wert muss zwischen {self.min_val} und {self.max_val} liegen.",
                ephemeral=True
            )
            return

        setattr(self.wizard, self.attr_name, val)
        self.wizard._update_labels()
        await interaction.response.edit_message(view=self.wizard)

class CategoryTextModal(discord.ui.Modal):
    def __init__(self, wizard: 'CategorySetupView', attr_name: str, title: str, placeholder: str, max_len: int, required: bool = True):
        super().__init__(title=title)
        self.wizard = wizard
        self.attr_name = attr_name
        self.text_input = discord.ui.TextInput(
            label=title,
            placeholder=placeholder,
            required=required,
            max_length=max_len
        )
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction):
        value = self.text_input.value if self.text_input.value else None
        setattr(self.wizard, self.attr_name, value)
        if self.attr_name == "emoji" and not value:
            self.wizard.emoji = "🎫"
        self.wizard._update_labels()
        await interaction.response.edit_message(view=self.wizard)

class CategorySetupView(discord.ui.View):
    def __init__(self, cog: "SupportCog", ctx: commands.Context, cat_id: str = None, cat_data: dict = None):
        super().__init__(timeout=300)
        self.cog = cog
        self.ctx = ctx
        self.cat_id = cat_id
        self.name = cat_data.get("name") if cat_data else None
        self.description = cat_data.get("description") if cat_data else None
        self.emoji = cat_data.get("emoji", "🎫") if cat_data else "🎫"
        self.abbr = cat_data.get("abbr", "TICKET") if cat_data else "TICKET"
        self.discord_category_id = cat_data.get("discord_category_id") if cat_data else None
        self.thread_parent_id = cat_data.get("thread_parent_id") if cat_data else None
        self.staff_role_id = cat_data.get("staff_role_id") if cat_data else None
        self.high_team_role_id = cat_data.get("high_team_role_id") if cat_data else None
        self.max_tickets = cat_data.get("max_tickets", 10) if cat_data else 10

        self._build_ui()

    def _build_ui(self):
        self.btn_name = discord.ui.Button(label="Name" if not self.name else f"Name: {self.name}", style=discord.ButtonStyle.primary, row=0)
        self.btn_name.callback = self._name_cb
        self.add_item(self.btn_name)

        self.btn_desc = discord.ui.Button(label="Beschreibung" if not self.description else "Beschreibung ✓", style=discord.ButtonStyle.secondary, row=0)
        self.btn_desc.callback = self._desc_cb
        self.add_item(self.btn_desc)

        self.btn_abbr = discord.ui.Button(label="Abkürzung" if not self.abbr else f"Abbr: {self.abbr}", style=discord.ButtonStyle.secondary, row=0)
        self.btn_abbr.callback = self._abbr_cb
        self.add_item(self.btn_abbr)

        self.btn_emoji = discord.ui.Button(label=f"Emoji: {self.emoji}", style=discord.ButtonStyle.secondary, row=0)
        self.btn_emoji.callback = self._emoji_cb
        self.add_item(self.btn_emoji)

        self.btn_max_tickets = discord.ui.Button(label=f"Max aktiv: {self.max_tickets}", style=discord.ButtonStyle.secondary, emoji='📊', row=1)
        self.btn_max_tickets.callback = self._max_tickets_cb
        self.add_item(self.btn_max_tickets)

        cat_options = [discord.SelectOption(label=cat.name[:100], value=str(cat.id)) for cat in self.ctx.guild.categories[:25]]
        if not cat_options:
            cat_options = [discord.SelectOption(label="Keine Kategorien", value="none")]
        self.disc_cat_sel = discord.ui.Select(placeholder="Discord Kategorie (für Channel-Typ)", options=cat_options, row=2)
        self.disc_cat_sel.callback = self._disc_cat_cb
        self.add_item(self.disc_cat_sel)

        thread_options = [discord.SelectOption(label=f"#{c.name}"[:100], value=str(c.id)) for c in self.ctx.guild.text_channels[:25]]
        if not thread_options:
            thread_options = [discord.SelectOption(label="Keine Textkanäle", value="none")]
        self.thread_sel = discord.ui.Select(placeholder="Thread-Channel (für Thread-Typ)", options=thread_options, row=3)
        self.thread_sel.callback = self._thread_cb
        self.add_item(self.thread_sel)

        staff_options = [discord.SelectOption(label=role.name[:100], value=str(role.id)) for role in self.ctx.guild.roles if not role.managed][:25]
        if not staff_options:
            staff_options = [discord.SelectOption(label="Keine Rollen", value="none")]
        self.staff_sel = discord.ui.Select(placeholder="Support-Rolle wählen", options=staff_options, row=4)
        self.staff_sel.callback = self._staff_cb
        self.add_item(self.staff_sel)

        high_options = staff_options.copy()
        self.high_sel = discord.ui.Select(placeholder="High-Team Rolle (Eskalation)", options=high_options, row=4)
        self.high_sel.callback = self._high_cb
        self.add_item(self.high_sel)

        self.btn_save = discord.ui.Button(label="Save" if not self.cat_id else "Update", style=discord.ButtonStyle.success, emoji='✅', row=1)
        self.btn_save.callback = self._save_cb
        self.add_item(self.btn_save)

    def _update_labels(self):
        self.btn_name.label = f"Name: {self.name}" if self.name else "Name"
        self.btn_desc.label = "Beschreibung ✓" if self.description else "Beschreibung"
        self.btn_abbr.label = f"Abbr: {self.abbr}" if self.abbr else "Abkürzung"
        self.btn_emoji.label = f"Emoji: {self.emoji}"
        self.btn_max_tickets.label = f"Max aktiv: {self.max_tickets}"

    async def _name_cb(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CategoryTextModal(self, "name", "Name der Kategorie", "z.B. Allgemeiner Support", max_len=50))

    async def _desc_cb(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CategoryTextModal(self, "description", "Beschreibung", "Wofür ist diese Kategorie?", max_len=100, required=False))

    async def _abbr_cb(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CategoryTextModal(self, "abbr", "Channel-Abkürzung", "z.B. SUP", max_len=10))

    async def _emoji_cb(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CategoryTextModal(self, "emoji", "Emoji", "Standard Emoji", max_len=10, required=False))

    async def _max_tickets_cb(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SimpleNumberModal(self, "max_tickets", "Maximale aktive Tickets (0 = unbegrenzt)", 0, 100))

    async def _disc_cat_cb(self, interaction: discord.Interaction):
        if self.disc_cat_sel.values[0] != "none":
            self.discord_category_id = int(self.disc_cat_sel.values[0])
            cat = self.ctx.guild.get_channel(self.discord_category_id)
            self.disc_cat_sel.placeholder = f"Kategorie: {cat.name}" if cat else "Discord Kategorie"
        else:
            self.discord_category_id = None
            self.disc_cat_sel.placeholder = "Discord Kategorie (für Channel-Typ)"
        await interaction.response.edit_message(view=self)

    async def _thread_cb(self, interaction: discord.Interaction):
        if self.thread_sel.values[0] != "none":
            self.thread_parent_id = int(self.thread_sel.values[0])
            ch = self.ctx.guild.get_channel(self.thread_parent_id)
            self.thread_sel.placeholder = f"Thread: #{ch.name}" if ch else "Thread-Channel"
        else:
            self.thread_parent_id = None
            self.thread_sel.placeholder = "Thread-Channel (für Thread-Typ)"
        await interaction.response.edit_message(view=self)

    async def _staff_cb(self, interaction: discord.Interaction):
        if self.staff_sel.values[0] != "none":
            self.staff_role_id = int(self.staff_sel.values[0])
            role = self.ctx.guild.get_role(self.staff_role_id)
            self.staff_sel.placeholder = f"Support: {role.name}" if role else "Support-Rolle"
        else:
            self.staff_role_id = None
            self.staff_sel.placeholder = "Support-Rolle wählen"
        await interaction.response.edit_message(view=self)

    async def _high_cb(self, interaction: discord.Interaction):
        if self.high_sel.values[0] != "none":
            self.high_team_role_id = int(self.high_sel.values[0])
            role = self.ctx.guild.get_role(self.high_team_role_id)
            self.high_sel.placeholder = f"High-Team: {role.name}" if role else "High-Team Rolle"
        else:
            self.high_team_role_id = None
            self.high_sel.placeholder = "High-Team Rolle (Eskalation)"
        await interaction.response.edit_message(view=self)

    async def _save_cb(self, interaction: discord.Interaction):
        if not self.name or not self.abbr or not self.staff_role_id:
            return await interaction.response.send_message("Bitte fülle Name, Abkürzung und Support-Rolle aus!", ephemeral=True)
        if not self.discord_category_id and not self.thread_parent_id:
            return await interaction.response.send_message("Bitte wähle entweder eine Discord Kategorie ODER einen Thread-Channel aus!", ephemeral=True)
        await self.cog.save_category(interaction, self, self.cat_id)
        self.stop()

class SupportCog(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=98765432123456789, force_registration=True)
        default_guild = {
            "panels": [],
            "log_channel_id": None,
            "dm_notifications": True,
            "categories": {},
            "active_tickets": [],
            "autoclose_hours": 48,
            "cooldown_minutes": 0,
            "blacklist": [],
            "stats": {},
            "max_tickets_per_user": 1,
            "total_tickets_created": 0,
            "delete_threads_after_close": False,
            "auto_escalate_hours": 0,
            "show_category_stats": True,
            "use_emoji_charts": True,
            "category_stats": {}
        }
        self.config.register_guild(**default_guild)

        self._active_channel_cache = {}
        self.autoclose_task = None
        self.init_task = None

    async def cog_load(self):
        self.init_task = self.bot.loop.create_task(self._async_init())

    async def _async_init(self):
        try:
            await self.bot.wait_until_ready()
            await self._initialize_views_and_cache()
            # self.autoclose_task = self.bot.loop.create_task(self.autoclose_loop())
        except Exception as e:
            log.error(f"SupportCog Initialisierung fehlgeschlagen: {e}")

    async def _initialize_views_and_cache(self):
        all_guilds = await self.config.all_guilds()
        for guild_id, data in all_guilds.items():
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            self._active_channel_cache[guild_id] = {
                t["channel_id"] for t in data.get("active_tickets", [])
            }

            # Panels und Views neu registrieren
            # (Dieser Teil war bereits in deinem Code vorhanden)

    def cog_unload(self):
        if self.autoclose_task:
            self.autoclose_task.cancel()
        if self.init_task and not self.init_task.done():
            self.init_task.cancel()

    # --- Cache-Helfer (Hier war dein Code abgeschnitten) ---
    def _add_to_active_cache(self, guild_id: int, channel_id: int):
        if guild_id not in self._active_channel_cache:
            self._active_channel_cache[guild_id] = set()
        self._active_channel_cache[guild_id].add(channel_id)

    # --- Befehle ---
    @commands.group(name="ticket")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def ticket(self, ctx):
        """Hauptbefehl für das Support Ticket System."""
        pass

    @ticket.command(name="addcat")
    async def ticket_addcat(self, ctx, cat_id: str = None):
        """Fügt eine neue Ticket-Kategorie hinzu oder bearbeitet sie."""
        if not cat_id:
            cat_id = str(uuid.uuid4())[:8]
            cat_data = {}
        else:
            categories = await self.config.guild(ctx.guild).categories()
            cat_data = categories.get(cat_id, {})

        view = CategorySetupView(self, ctx, cat_id, cat_data)
        await ctx.send(f"**Setup für Kategorie:** `{cat_id}`\nBitte klicke auf die Buttons, um die Kategorie zu konfigurieren.", view=view)

    # --- Fehlende Methoden für Views ---
    async def save_category(self, interaction: discord.Interaction, view: CategorySetupView, cat_id: str):
        """Wird aufgerufen, wenn im CategorySetupView auf 'Save' geklickt wird."""
        async with self.config.guild(interaction.guild).categories() as categories:
            if cat_id is None:
                cat_id = str(uuid.uuid4())[:8]

            categories[cat_id] = {
                "name": view.name,
                "description": view.description,
                "emoji": view.emoji,
                "abbr": view.abbr,
                "discord_category_id": view.discord_category_id,
                "thread_parent_id": view.thread_parent_id,
                "staff_role_id": view.staff_role_id,
                "high_team_role_id": view.high_team_role_id,
                "max_tickets": view.max_tickets
            }
        
        await interaction.response.send_message(f"✅ Kategorie `{view.name}` erfolgreich gespeichert!", ephemeral=True)
        # Optional: update panels here if needed.
