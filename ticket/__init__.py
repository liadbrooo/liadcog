"""
SupportCog V26 - Final Stable & Robust
- Asynchrone Initialisierung verhindert Ladefehler
- Alle vorherigen Fehler behoben
- Kompatibel mit RedBot und discord.py 2.x
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

    @discord.ui.string_select(
        placeholder="🎫 Wähle hier eine Kategorie für dein Ticket aus...",
        custom_id='support_ticket_create_select',
        min_values=1,
        max_values=1,
        options=[discord.SelectOption(label="Lädt...", value="loading")]
    )
    async def create_ticket_select(self, interaction: discord.Interaction, select: discord.ui.StringSelect):
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

        # Überlastungsprüfung pro Kategorie
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

    @discord.ui.string_select(
        placeholder="Ticket Status ändern...",
        custom_id='support_ticket_status_select',
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="Aktiv", value="ACTIVE", emoji="🟢", description="Normales Ticket."),
            discord.SelectOption(label="Wartet auf User", value="WAITING_USER", emoji="🟡", description="Team wartet auf Antwort. (Auto-Close läuft)"),
            discord.SelectOption(label="Wartet auf Team", value="WAITING_TEAM", emoji="🔴", description="Team prüft intern. (Auto-Close pausiert)"),
            discord.SelectOption(label="Pausiert", value="PAUSED", emoji="⏸️", description="Ticket ist pausiert.")
        ]
    )
    async def status_select(self, interaction: discord.Interaction, select: discord.ui.StringSelect):
        await self.cog.change_status(interaction, select.values[0], self)


class ReviewView(discord.ui.View):
    def __init__(self, cog: "SupportCog", ticket_data: dict):
        super().__init__(timeout=60)
        self.cog = cog
        self.ticket_data = ticket_data
        self.message = None

        for i in range(1, 6):
            button = discord.ui.Button(
                label='⭐',
                custom_id=f'review_star_{i}',
                style=discord.ButtonStyle.secondary
            )
            button.callback = self.review_stars
            self.add_item(button)

    async def review_stars(self, interaction: discord.Interaction):
        if interaction.user.id != self.ticket_data["user_id"]:
            return await interaction.response.send_message(
                "Nur der Ticket-Ersteller kann bewerten.",
                ephemeral=True
            )

        stars = int(interaction.data["custom_id"][-1])
        await interaction.response.edit_message(
            content=f"Danke für dein Feedback ({stars}⭐)! Der Channel wird in 5 Sekunden geschlossen...",
            view=None
        )
        await self.cog.delete_ticket_channel(interaction.channel, self.ticket_data, stars)

    async def on_timeout(self):
        if self.message:
            await self.cog.delete_ticket_channel(self.message.channel, self.ticket_data, 0)


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
            max_length=5
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
        self.wizard.update_ui()
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
        self.wizard.update_ui()
        await interaction.response.edit_message(view=self.wizard)


class BaseSetupView(discord.ui.View):
    def __init__(self, cog: "SupportCog", ctx: commands.Context):
        super().__init__(timeout=300)
        self.cog = cog
        self.ctx = ctx
        self.log_channel_id = None
        self.dm_notifications = True
        self.autoclose_hours = 48
        self.cooldown_minutes = 0
        self.max_tickets_per_user = 1
        self.delete_threads_after_close = False
        self.auto_escalate_hours = 0
        self.show_category_stats = True
        self.use_emoji_charts = True
        self.update_ui()

    def update_ui(self):
        self.clear_items()

        # Row 0: Log-Channel Auswahl
        log_ph = "Log-Channel wählen"
        if self.log_channel_id:
            ch = self.ctx.guild.get_channel(self.log_channel_id)
            if ch:
                log_ph = f"Log-Channel: {ch.name}"
        log_sel = discord.ui.ChannelSelect(
            placeholder=log_ph,
            channel_types=[discord.ChannelType.text],
            row=0
        )
        async def log_cb(inter: discord.Interaction):
            self.log_channel_id = log_sel.values[0].id
            self.update_ui()
            await inter.response.edit_message(view=self)
        log_sel.callback = log_cb
        self.add_item(log_sel)

        # Row 1: DM-Benachrichtigungen, Auto-Close, Cooldown, Max Tickets
        btn_dm = discord.ui.Button(
            label=f"DMs: {'AN' if self.dm_notifications else 'AUS'}",
            style=discord.ButtonStyle.success if self.dm_notifications else discord.ButtonStyle.danger,
            emoji='✉️',
            row=1
        )
        async def dm_cb(inter: discord.Interaction):
            self.dm_notifications = not self.dm_notifications
            self.update_ui()
            await inter.response.edit_message(view=self)
        btn_dm.callback = dm_cb
        self.add_item(btn_dm)

        btn_auto = discord.ui.Button(
            label=f"Auto-Close: {self.autoclose_hours}h",
            style=discord.ButtonStyle.secondary,
            emoji='⏳',
            row=1
        )
        async def auto_cb(inter: discord.Interaction):
            await inter.response.send_modal(
                SimpleNumberModal(self, "autoclose_hours", "Auto-Close (Stunden)", 0, 500)
            )
        btn_auto.callback = auto_cb
        self.add_item(btn_auto)

        btn_cool = discord.ui.Button(
            label=f"Cooldown: {self.cooldown_minutes}m",
            style=discord.ButtonStyle.secondary,
            emoji='❄️',
            row=1
        )
        async def cool_cb(inter: discord.Interaction):
            await inter.response.send_modal(
                SimpleNumberModal(self, "cooldown_minutes", "Cooldown (Minuten)", 0, 10080)
            )
        btn_cool.callback = cool_cb
        self.add_item(btn_cool)

        btn_max = discord.ui.Button(
            label=f"Max Tickets: {self.max_tickets_per_user}",
            style=discord.ButtonStyle.secondary,
            emoji='🔢',
            row=1
        )
        async def max_cb(inter: discord.Interaction):
            await inter.response.send_modal(
                SimpleNumberModal(self, "max_tickets_per_user", "Max Tickets pro User", 1, 10)
            )
        btn_max.callback = max_cb
        self.add_item(btn_max)

        # Row 2: Neue Optionen
        btn_del_thread = discord.ui.Button(
            label=f"Threads löschen: {'AN' if self.delete_threads_after_close else 'AUS'}",
            style=discord.ButtonStyle.success if self.delete_threads_after_close else discord.ButtonStyle.danger,
            emoji='🗑️',
            row=2
        )
        async def del_thread_cb(inter: discord.Interaction):
            self.delete_threads_after_close = not self.delete_threads_after_close
            self.update_ui()
            await inter.response.edit_message(view=self)
        btn_del_thread.callback = del_thread_cb
        self.add_item(btn_del_thread)

        btn_esc = discord.ui.Button(
            label=f"Auto-Eskalation: {self.auto_escalate_hours}h",
            style=discord.ButtonStyle.secondary,
            emoji='🚨',
            row=2
        )
        async def esc_cb(inter: discord.Interaction):
            await inter.response.send_modal(
                SimpleNumberModal(self, "auto_escalate_hours", "Auto-Eskalation nach Stunden (0=aus)", 0, 500)
            )
        btn_esc.callback = esc_cb
        self.add_item(btn_esc)

        btn_catstats = discord.ui.Button(
            label=f"Kategorie-Statistiken: {'AN' if self.show_category_stats else 'AUS'}",
            style=discord.ButtonStyle.success if self.show_category_stats else discord.ButtonStyle.danger,
            emoji='📊',
            row=2
        )
        async def catstats_cb(inter: discord.Interaction):
            self.show_category_stats = not self.show_category_stats
            self.update_ui()
            await inter.response.edit_message(view=self)
        btn_catstats.callback = catstats_cb
        self.add_item(btn_catstats)

        btn_emoji = discord.ui.Button(
            label=f"Emoji-Balken: {'AN' if self.use_emoji_charts else 'AUS'}",
            style=discord.ButtonStyle.success if self.use_emoji_charts else discord.ButtonStyle.danger,
            emoji='📈',
            row=2
        )
        async def emoji_cb(inter: discord.Interaction):
            self.use_emoji_charts = not self.use_emoji_charts
            self.update_ui()
            await inter.response.edit_message(view=self)
        btn_emoji.callback = emoji_cb
        self.add_item(btn_emoji)

        # Row 3: Fertig
        btn_finish = discord.ui.Button(
            label="Setup abschließen",
            style=discord.ButtonStyle.success,
            emoji='✅',
            row=3
        )
        async def finish_cb(inter: discord.Interaction):
            if not self.log_channel_id:
                return await inter.response.send_message(
                    "Bitte wähle zuerst einen Log-Channel aus!",
                    ephemeral=True
                )
            await self.cog.finish_base_setup(inter, self)
            self.stop()
        btn_finish.callback = finish_cb
        self.add_item(btn_finish)


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
        self.update_ui()

    def update_ui(self):
        self.clear_items()

        btn_name = discord.ui.Button(
            label="Name" if not self.name else "Name ✅",
            style=discord.ButtonStyle.primary,
            row=0
        )
        async def name_cb(inter: discord.Interaction):
            await inter.response.send_modal(
                CategoryTextModal(self, "name", "Name der Kategorie", "z.B. Allgemeiner Support", max_len=50)
            )
        btn_name.callback = name_cb
        self.add_item(btn_name)

        btn_desc = discord.ui.Button(
            label="Beschreibung" if not self.description else "Besch. ✅",
            style=discord.ButtonStyle.secondary,
            row=0
        )
        async def desc_cb(inter: discord.Interaction):
            await inter.response.send_modal(
                CategoryTextModal(self, "description", "Beschreibung", "Wofür ist diese Kategorie?", max_len=100)
            )
        btn_desc.callback = desc_cb
        self.add_item(btn_desc)

        btn_abbr = discord.ui.Button(
            label="Abkürzung" if not self.abbr else f"Abbr: {self.abbr}",
            style=discord.ButtonStyle.secondary,
            row=0
        )
        async def abbr_cb(inter: discord.Interaction):
            await inter.response.send_modal(
                CategoryTextModal(self, "abbr", "Channel-Abkürzung", "z.B. SUP", max_len=10)
            )
        btn_abbr.callback = abbr_cb
        self.add_item(btn_abbr)

        btn_emoji = discord.ui.Button(
            label=f"Emoji: {self.emoji}",
            style=discord.ButtonStyle.secondary,
            row=0
        )
        async def emoji_cb(inter: discord.Interaction):
            await inter.response.send_modal(
                CategoryTextModal(self, "emoji", "Emoji", "Standard Emoji", max_len=10, required=False)
            )
        btn_emoji.callback = emoji_cb
        self.add_item(btn_emoji)

        # Button für Max Tickets (Überlastung)
        btn_max_tickets = discord.ui.Button(
            label=f"Max aktiv: {self.max_tickets}",
            style=discord.ButtonStyle.secondary,
            emoji='📊',
            row=1
        )
        async def max_tickets_cb(inter: discord.Interaction):
            await inter.response.send_modal(
                SimpleNumberModal(self, "max_tickets", "Maximale aktive Tickets (0 = unbegrenzt)", 0, 100)
            )
        btn_max_tickets.callback = max_tickets_cb
        self.add_item(btn_max_tickets)

        btn_save = discord.ui.Button(
            label="Save" if not self.cat_id else "Update",
            style=discord.ButtonStyle.success,
            emoji='✅',
            row=1
        )
        async def save_cb(inter: discord.Interaction):
            if not all([self.name, self.abbr, self.staff_role_id]):
                return await inter.response.send_message(
                    "Bitte fülle Name, Abkürzung und Support-Rolle aus!",
                    ephemeral=True
                )
            if not self.discord_category_id and not self.thread_parent_id:
                return await inter.response.send_message(
                    "Bitte wähle entweder eine Discord Kategorie ODER einen Thread-Channel aus!",
                    ephemeral=True
                )
            await self.cog.save_category(inter, self, self.cat_id)
            self.stop()
        btn_save.callback = save_cb
        self.add_item(btn_save)

        disc_cat_ph = "Discord Kategorie (für Channel-Typ)"
        if self.discord_category_id:
            ch = self.ctx.guild.get_channel(self.discord_category_id)
            if ch:
                disc_cat_ph = f"Kategorie: {ch.name}"
        disc_cat_sel = discord.ui.ChannelSelect(
            placeholder=disc_cat_ph,
            channel_types=[discord.ChannelType.category],
            row=2
        )
        async def disc_cat_cb(inter: discord.Interaction):
            self.discord_category_id = disc_cat_sel.values[0].id
            self.update_ui()
            await inter.response.edit_message(view=self)
        disc_cat_sel.callback = disc_cat_cb
        self.add_item(disc_cat_sel)

        thread_par_ph = "Thread-Channel (für Thread-Typ)"
        if self.thread_parent_id:
            ch = self.ctx.guild.get_channel(self.thread_parent_id)
            if ch:
                thread_par_ph = f"Thread-Channel: {ch.name}"
        thread_par_sel = discord.ui.ChannelSelect(
            placeholder=thread_par_ph,
            channel_types=[discord.ChannelType.text],
            row=3
        )
        async def thread_par_cb(inter: discord.Interaction):
            self.thread_parent_id = thread_par_sel.values[0].id
            self.update_ui()
            await inter.response.edit_message(view=self)
        thread_par_sel.callback = thread_par_cb
        self.add_item(thread_par_sel)

        staff_ph = "Support-Rolle wählen"
        if self.staff_role_id:
            r = self.ctx.guild.get_role(self.staff_role_id)
            if r:
                staff_ph = f"Support: {r.name}"
        staff_sel = discord.ui.RoleSelect(
            placeholder=staff_ph,
            row=4
        )
        async def staff_cb(inter: discord.Interaction):
            self.staff_role_id = staff_sel.values[0].id
            self.update_ui()
            await inter.response.edit_message(view=self)
        staff_sel.callback = staff_cb
        self.add_item(staff_sel)

        high_ph = "High-Team Rolle (Eskalation)"
        if self.high_team_role_id:
            r = self.ctx.guild.get_role(self.high_team_role_id)
            if r:
                high_ph = f"High-Team: {r.name}"
        high_sel = discord.ui.RoleSelect(
            placeholder=high_ph,
            row=4
        )
        async def high_cb(inter: discord.Interaction):
            self.high_team_role_id = high_sel.values[0].id
            self.update_ui()
            await inter.response.edit_message(view=self)
        high_sel.callback = high_cb
        self.add_item(high_sel)


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

        # Performance-Cache: Guild-ID -> Set von aktiven Ticket-Kanal-IDs
        self._active_channel_cache = {}

        self.autoclose_task = None
        self.init_task = None

    async def cog_load(self):
        # Asynchrone Initialisierung starten, um Blockaden zu vermeiden
        self.init_task = self.bot.loop.create_task(self._async_init())

    async def _async_init(self):
        try:
            await self.bot.wait_until_ready()
            await self._initialize_views_and_cache()
            self.autoclose_task = self.bot.loop.create_task(self.autoclose_loop())
        except Exception as e:
            log.error(f"SupportCog Initialisierung fehlgeschlagen: {e}")

    async def _initialize_views_and_cache(self):
        all_guilds = await self.config.all_guilds()
        for guild_id, data in all_guilds.items():
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            # Cache aufbauen
            self._active_channel_cache[guild_id] = {
                t["channel_id"] for t in data.get("active_tickets", [])
            }

            # Panels registrieren
            categories = data.get("categories", {})
            active_tickets = data.get("active_tickets", [])
            for panel in data.get("panels", []):
                try:
                    view = TicketPanelView(self)
                    if categories:
                        options = []
                        for cat_id, c in categories.items():
                            active_count = sum(
                                1 for t in active_tickets
                                if t["cat_id"] == cat_id and t.get("status") == "ACTIVE"
                            )
                            max_t = c.get("max_tickets", 10)
                            label = c["name"][:100]
                            if max_t > 0:
                                label = f"{c['name']} ({active_count}/{max_t})"[:100]
                                description = f"{c.get('description', '')} - {int((active_count/max_t)*100)}% ausgelastet"[:100] if c.get('description') else f"{int((active_count/max_t)*100)}% ausgelastet"
                            else:
                                description = c.get("description", "")[:100] if c.get("description") else None

                            options.append(
                                discord.SelectOption(
                                    label=label,
                                    value=cat_id,
                                    description=description,
                                    emoji=c.get("emoji")
                                )
                            )
                        options = options[:25]
                        for child in view.children:
                            if isinstance(child, discord.ui.StringSelect):
                                child.options = options
                    else:
                        view.clear_items()
                    self.bot.add_view(view, message_id=panel["msg_id"])
                except Exception as e:
                    log.error(f"Panel-Registrierung fehlgeschlagen für {panel}: {e}")

            # Ticket-Control-Views registrieren
            for ticket in data.get("active_tickets", []):
                if "panel_msg_id" in ticket and ticket["panel_msg_id"]:
                    try:
                        view = TicketControlView(self)
                        if ticket.get("claimed_by"):
                            for child in view.children:
                                if child.custom_id == "support_ticket_claim_btn":
                                    child.label = "Freigeben"
                                    child.style = discord.ButtonStyle.secondary
                        if ticket.get("escalated"):
                            for child in view.children:
                                if child.custom_id == "support_ticket_escalate_btn":
                                    child.disabled = True
                        self.bot.add_view(view, message_id=ticket["panel_msg_id"])
                    except Exception as e:
                        log.error(f"Ticket-Control-View Registrierung fehlgeschlagen: {e}")

    def cog_unload(self):
        if self.autoclose_task:
            self.autoclose_task.cancel()
        if self.init_task and not self.init_task.done():
            self.init_task.cancel()

    # --- Cache-Helfer ---
    def _add_to_active_cache(self, guild_id: int, channel_id: int):
        if guild_id not in self._active_channel_cache:
            self._active_channel_cache[guild_id] = set()
        self._active_channel_cache[guild_id].add(channel_id)

    def _remove_from_active_cache(self, guild_id: int, channel_id: int):
        if guild_id in self._active_channel_cache:
            self._active_channel_cache[guild_id].discard(channel_id)

    # --- Helpers ---
    async def send_dm(self, user: discord.User, title: str, description: str):
        try:
            embed = discord.Embed(
                title=title,
                description=description,
                color=discord.Color.blurple(),
                timestamp=datetime.datetime.now()
            )
            await user.send(embed=embed)
        except Exception:
            pass

    async def send_log(self, guild: discord.Guild, title: str, color: discord.Color, fields: list):
        try:
            log_ch_id = await self.config.guild(guild).log_channel_id()
            if not log_ch_id:
                return
            log_ch = guild.get_channel(log_ch_id)
            if not log_ch:
                return

            embed = discord.Embed(title=title, color=color, timestamp=datetime.datetime.now())
            for name, value in fields:
                embed.add_field(name=name, value=str(value)[:1024], inline=False)
            await log_ch.send(embed=embed)
        except Exception as e:
            log.error(f"Failed to send log message: {e}")

    def _emoji_bar(self, value, max_value, length=10):
        """Erzeugt einen Emoji-Balken (z.B. 🟩🟩🟩⬜⬜...)."""
        if max_value <= 0:
            return "⬜" * length
        filled = int((value / max_value) * length)
        filled = max(0, min(filled, length))
        return "🟩" * filled + "⬜" * (length - filled)

    # --- Listener ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        # Performance: Nur prüfen, wenn Kanal als aktiver Ticketkanal im Cache ist
        if message.channel.id not in self._active_channel_cache.get(message.guild.id, set()):
            return

        try:
            tickets = await self.config.guild(message.guild).active_tickets()
            for t in tickets:
                if t["channel_id"] == message.channel.id:
                    if t.get("status") == "PAUSED" and not message.author.guild_permissions.manage_messages:
                        try:
                            await message.delete()
                            await message.channel.send(
                                f"{message.author.mention}, dieses Ticket ist pausiert. Du kannst aktuell nicht schreiben.",
                                delete_after=5
                            )
                        except Exception:
                            pass
                        return
                    t["last_message"] = datetime.datetime.now().isoformat()
                    if t.get("status") == "WAITING_USER":
                        t["warned"] = False
                    await self.config.guild(message.guild).active_tickets.set(tickets)
                    break
        except Exception as e:
            log.error(f"Error in on_message: {e}")

    # --- Auto-Close & Auto-Eskalation Loop ---
    async def autoclose_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                for guild_id, data in (await self.config.all_guilds()).items():
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        continue

                    ah = data.get("autoclose_hours", 48)
                    auto_esc_hours = data.get("auto_escalate_hours", 0)
                    tickets = data.get("active_tickets", [])
                    changed = False

                    for t in tickets[:]:
                        ch = guild.get_channel(t["channel_id"])
                        if not ch:
                            tickets.remove(t)
                            self._remove_from_active_cache(guild_id, t["channel_id"])
                            changed = True
                            continue

                        # Auto-Close nur bei ACTIVE oder WAITING_USER (WAITING_TEAM und PAUSED pausieren)
                        if t.get("status") in ["WAITING_TEAM", "PAUSED"]:
                            if t.get("status") == "WAITING_TEAM" and auto_esc_hours > 0 and not t.get("escalated"):
                                lm = datetime.datetime.fromisoformat(t.get("last_message", datetime.datetime.now().isoformat()))
                                diff_h = (datetime.datetime.now() - lm).total_seconds() / 3600
                                if diff_h > auto_esc_hours:
                                    # Auto-Eskalation auslösen
                                    await self.auto_escalate_ticket(guild, t, ch)
                                    t["escalated"] = True
                                    t["last_message"] = datetime.datetime.now().isoformat()
                                    changed = True
                            # last_message aktualisieren, damit Auto-Close nicht greift
                            t["last_message"] = datetime.datetime.now().isoformat()
                            changed = True
                            continue

                        # Auto-Close Logik
                        if ah == 0:
                            continue

                        lm = datetime.datetime.fromisoformat(t.get("last_message", datetime.datetime.now().isoformat()))
                        diff_h = (datetime.datetime.now() - lm).total_seconds() / 3600

                        if diff_h > (ah - 2) and not t.get("warned", False):
                            try:
                                await ch.send(
                                    f"⚠️ <@{t['user_id']}>, dieses Ticket wird in **2 Stunden** automatisch geschlossen, wenn nicht geantwortet wird."
                                )
                                t["warned"] = True
                                changed = True
                            except Exception:
                                pass

                        if diff_h > ah:
                            await self.close_ticket(ch, "Inaktivität (Auto-Close)", guild.me, is_auto=True)
                            tickets.remove(t)
                            changed = True

                    if changed:
                        await self.config.guild(guild).active_tickets.set(tickets)
            except Exception as e:
                log.error(f"Autoclose Loop Error: {e}")
            await asyncio.sleep(300)

    # --- Befehle ---
    @commands.group(name="ticket", aliases=["tickets"], invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def ticket_cmd(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @ticket_cmd.command(name="help")
    async def ticket_help(self, ctx: commands.Context):
        """Zeigt eine ausführliche Anleitung für das Ticket-System."""
        embed = discord.Embed(
            title="🎫 Ticket System – Hilfe & Anleitung",
            description="Willkommen beim ultimativen Ticket-System! Hier findest du alle wichtigen Informationen.",
            color=discord.Color.blurple()
        )
        embed.add_field(
            name="🔧 Setup (nur Admins)",
            value=(
                "1. `[p]ticket setup` – Basis-Konfiguration (Log-Channel, DMs, Auto-Close, Cooldown, Max Tickets, Thread-Löschung, Auto-Eskalation, Statistiken).\n"
                "2. `[p]ticket addcat` – Eine Support-Kategorie erstellen (Name, Beschreibung, Emoji, Abkürzung, Discord-Kategorie oder Thread-Channel, Support-Rolle, High-Team-Rolle, maximale Auslastung).\n"
                "3. `[p]ticket postpanel #channel` – Das Ticket-Panel in einem gewünschten Channel posten.\n"
                "4. Optional: `[p]ticket blacklist @User` / `unblacklist @User` – Nutzer sperren/entsperren.\n"
                "5. Optional: `[p]ticket managecats` – Kategorien nachträglich bearbeiten oder löschen.\n"
                "6. `[p]ticket stats [Kategorie]` – Statistiken anzeigen (optional für eine Kategorie).\n"
                "7. `[p]ticket export` – CSV-Export aller Statistiken."
            ),
            inline=False
        )
        embed.add_field(
            name="👤 Für Nutzer",
            value=(
                "1. Klicke im Ticket-Panel auf das Dropdown-Menü und wähle eine Kategorie.\n"
                "2. Fülle das kurze Formular aus (Beschreibung deines Anliegens).\n"
                "3. Dein Ticket wird als eigener Channel (oder Thread) erstellt. Das Support-Team wird dir dort antworten.\n"
                "4. Nach dem Schließen erhältst du eine Bewertungsmöglichkeit und das Transkript als HTML-Datei per DM."
            ),
            inline=False
        )
        embed.add_field(
            name="🛠️ Support-Team Befehle (im Ticket-Channel)",
            value=(
                "`[p]tadd @User` – Einen Nutzer zum Ticket hinzufügen.\n"
                "`[p]tremove @User` – Einen Nutzer aus dem Ticket entfernen.\n"
                "`[p]trename NeuerName` – Ticket umbenennen.\n"
                "`[p]ticket forceclose` – Ticket sofort schließen (ohne Transkript/Review).\n\n"
                "**Buttons im Ticket:**\n"
                "✋ Übernehmen – Ticket als Bearbeiter übernehmen.\n"
                "⚠️ Eskalieren – High-Team hinzuziehen (falls konfiguriert).\n"
                "🔒 Schließen – Ticket mit Grund schließen.\n"
                "Status-Dropdown – Ticketstatus ändern (beeinflusst Auto-Close und Berechtigungen)."
            ),
            inline=False
        )
        embed.add_field(
            name="⏱️ Status-Erklärung",
            value=(
                "🟢 Aktiv – Normales Ticket, Auto-Close läuft.\n"
                "🟡 Wartet auf User – Team wartet auf Antwort des Users, Auto-Close läuft.\n"
                "🔴 Wartet auf Team – Team prüft intern, Auto-Close pausiert. Auto-Eskalation kann aktiv sein.\n"
                "⏸️ Pausiert – Ticket komplett pausiert, User kann nicht schreiben."
            ),
            inline=False
        )
        embed.add_field(
            name="📊 Überlastungssystem",
            value=(
                "Jede Kategorie hat ein Limit für gleichzeitig aktive Tickets (Standard: 10).\n"
                "Im Panel wird die Auslastung angezeigt (z.B. `Support (3/10)`).\n"
                "Sobald das Limit erreicht ist, können keine neuen Tickets in dieser Kategorie erstellt werden.\n"
                "Tickets mit Status `Wartet auf Team` oder `Pausiert` zählen **nicht** zur Auslastung."
            ),
            inline=False
        )
        embed.add_field(
            name="📈 Statistiken & Export",
            value=(
                "`[p]ticket stats` – Zeigt Gesamtübersicht, Leaderboard und Kategorie-Statistiken.\n"
                "`[p]ticket stats Kategoriename` – Detailansicht für eine Kategorie.\n"
                "`[p]ticket export` – CSV-Datei mit allen Team- und Kategorie-Statistiken.\n"
                "Emoji-Balken (falls aktiviert) visualisieren die Verteilung."
            ),
            inline=False
        )
        embed.set_footer(text="Bei Fragen wende dich an einen Administrator.")
        await ctx.send(embed=embed)

    @ticket_cmd.command(name="setup")
    async def ticket_setup(self, ctx: commands.Context):
        view = BaseSetupView(self, ctx)
        msg = await ctx.send(
            embed=discord.Embed(
                title="🛠️ Ticket Basis-Setup",
                description="Konfiguriere alle grundlegenden Optionen. Klicke auf die Buttons, um Werte zu ändern.",
                color=discord.Color.blurple()
            ),
            view=view
        )
        view.message = msg

    @ticket_cmd.command(name="addcat")
    async def ticket_addcat(self, ctx: commands.Context):
        view = CategorySetupView(self, ctx)
        msg = await ctx.send(
            embed=discord.Embed(
                title="🏷️ Kategorie Setup",
                description="Konfiguriere alle Werte. Wähle Channel ODER Thread.",
                color=discord.Color.green()
            ),
            view=view
        )
        view.message = msg

    @ticket_cmd.command(name="managecats")
    async def ticket_managecats(self, ctx: commands.Context):
        categories = await self.config.guild(ctx.guild).categories()
        if not categories:
            return await ctx.send("❌ Es existieren noch keine Kategorien.")

        options = [
            discord.SelectOption(
                label=c["name"][:100],
                value=cat_id,
                description="Zum Bearbeiten/Löschen",
                emoji=c.get("emoji")
            )
            for cat_id, c in categories.items()
        ][:25]

        view = discord.ui.View(timeout=300)
        select = discord.ui.StringSelect(placeholder="Wähle eine Kategorie aus...", options=options)

        async def select_cb(inter: discord.Interaction):
            if inter.user != ctx.author:
                return await inter.response.send_message("Nur du kannst das.", ephemeral=True)

            cat_id = select.values[0]
            cat_data = categories[cat_id]
            ed_view = discord.ui.View(timeout=300)

            btn_edit = discord.ui.Button(label="Bearbeiten", style=discord.ButtonStyle.primary, emoji="✏️")
            btn_del = discord.ui.Button(label="Löschen", style=discord.ButtonStyle.danger, emoji="🗑️")
            btn_back = discord.ui.Button(label="Abbrechen", style=discord.ButtonStyle.secondary, emoji="⬅️")

            async def edit_cb(inter2: discord.Interaction):
                setup_view = CategorySetupView(self, ctx, cat_id=cat_id, cat_data=cat_data)
                await inter2.response.edit_message(
                    embed=discord.Embed(
                        title="✏️ Kategorie bearbeiten",
                        description="Passe die Werte an und klicke auf 'Update durchführen'.",
                        color=discord.Color.orange()
                    ),
                    view=setup_view
                )
                setup_view.message = inter2.message

            async def del_cb(inter2: discord.Interaction):
                del categories[cat_id]
                await self.config.guild(ctx.guild).categories.set(categories)
                await self.update_panels(ctx.guild)
                await inter2.response.edit_message(
                    content=f"✅ Kategorie '{cat_data['name']}' wurde gelöscht.",
                    embed=None,
                    view=None
                )

            async def back_cb(inter2: discord.Interaction):
                await inter2.response.edit_message(content="Abgebrochen.", embed=None, view=None)

            btn_edit.callback = edit_cb
            btn_del.callback = del_cb
            btn_back.callback = back_cb
            ed_view.add_item(btn_edit)
            ed_view.add_item(btn_del)
            ed_view.add_item(btn_back)

            await inter.response.edit_message(
                content=f"Ausgewählt: **{cat_data['name']}**. Was möchtest du tun?",
                embed=None,
                view=ed_view
            )

        select.callback = select_cb
        view.add_item(select)
        await ctx.send("Wähle eine Kategorie aus, um sie zu bearbeiten oder zu löschen:", view=view)

    @ticket_cmd.command(name="forceclose")
    async def ticket_forceclose(self, ctx: commands.Context):
        tickets = await self.config.guild(ctx.guild).active_tickets()
        t_data = next((t for t in tickets if t["channel_id"] == ctx.channel.id), None)
        if t_data:
            tickets.remove(t_data)
            await self.config.guild(ctx.guild).active_tickets.set(tickets)
            self._remove_from_active_cache(ctx.guild.id, ctx.channel.id)
            await ctx.send("⚠️ Ticket wird zwangsweise geschlossen (kein Transkript, kein Review)...")
            delete_threads = await self.config.guild(ctx.guild).delete_threads_after_close()
            try:
                if isinstance(ctx.channel, discord.Thread):
                    if delete_threads:
                        await ctx.channel.delete()
                    else:
                        await ctx.channel.edit(archived=True, locked=True)
                else:
                    await ctx.channel.delete(reason="Force closed by Admin")
            except Exception:
                pass
        else:
            await ctx.send("❌ Dies ist kein aktives Ticket.")

    @ticket_cmd.command(name="postpanel")
    async def ticket_postpanel(self, ctx: commands.Context, channel: discord.TextChannel):
        await self.create_panel(channel)
        await ctx.send(f"✅ Panel in {channel.mention} gepostet.")

    @ticket_cmd.command(name="blacklist")
    async def ticket_blacklist(self, ctx: commands.Context, user: discord.User, *, reason: str = "Kein Grund angegeben"):
        bl = await self.config.guild(ctx.guild).blacklist()
        if user.id not in bl:
            bl.append(user.id)
            await self.config.guild(ctx.guild).blacklist.set(bl)
            await ctx.send(f"✅ {user.mention} wurde gesperrt. Grund: {reason}")
        else:
            await ctx.send("❌ Bereits gesperrt.")

    @ticket_cmd.command(name="unblacklist")
    async def ticket_unblacklist(self, ctx: commands.Context, user: discord.User):
        bl = await self.config.guild(ctx.guild).blacklist()
        if user.id in bl:
            bl.remove(user.id)
            await self.config.guild(ctx.guild).blacklist.set(bl)
            await ctx.send(f"✅ {user.mention} entsperrt.")
        else:
            await ctx.send("❌ Nicht gesperrt.")

    @ticket_cmd.command(name="stats")
    async def ticket_stats(self, ctx: commands.Context, category: str = None):
        """Zeigt erweiterte Statistiken. Optional: `[p]ticket stats Kategoriename`."""
        stats = await self.config.guild(ctx.guild).stats()
        active_tickets = await self.config.guild(ctx.guild).active_tickets()
        total_created = await self.config.guild(ctx.guild).total_tickets_created()
        category_stats = await self.config.guild(ctx.guild).category_stats()
        categories = await self.config.guild(ctx.guild).categories()

        if not stats and not active_tickets and total_created == 0 and not category_stats:
            return await ctx.send("Noch keine Statistiken vorhanden.")

        # Wenn Kategorie angegeben, filtere
        if category:
            cat_data = None
            for cat_id, c in categories.items():
                if c["name"].lower() == category.lower():
                    cat_data = c
                    cat_id_found = cat_id
                    break
            if not cat_data:
                return await ctx.send("❌ Kategorie nicht gefunden.")

            cat_stat = category_stats.get(cat_id_found, {"created": 0, "closed": 0, "stars": [0,0,0,0,0], "total_duration_minutes": 0, "ticket_count": 0})
            embed = discord.Embed(
                title=f"📊 Statistiken für Kategorie: {cat_data['name']}",
                color=discord.Color.gold()
            )
            embed.add_field(name="Erstellt", value=cat_stat.get("created", 0), inline=True)
            embed.add_field(name="Geschlossen", value=cat_stat.get("closed", 0), inline=True)
            embed.add_field(name="Offen", value=sum(1 for t in active_tickets if t["cat_id"] == cat_id_found), inline=True)
            if cat_stat.get("ticket_count", 0) > 0:
                avg_duration = cat_stat.get("total_duration_minutes", 0) / cat_stat["ticket_count"]
            else:
                avg_duration = 0
            embed.add_field(name="Ø Bearbeitungszeit", value=f"{avg_duration:.1f} Min", inline=True)
            if sum(cat_stat.get("stars", [0,0,0,0,0])) > 0:
                avg_rating = sum((i+1)*s for i,s in enumerate(cat_stat["stars"])) / sum(cat_stat["stars"])
            else:
                avg_rating = 0
            embed.add_field(name="Ø Bewertung", value=f"{avg_rating:.2f} / 5", inline=True)
            # Emoji-Balken für Bewertungen (falls aktiv)
            if await self.config.guild(ctx.guild).use_emoji_charts():
                bar = self._emoji_bar(avg_rating, 5, 10)
                embed.add_field(name="Bewertungs-Balken", value=bar, inline=False)
            return await ctx.send(embed=embed)

        # Gesamtübersicht
        total_closed = sum(u.get("closed", 0) for u in stats.values())
        total_open = len(active_tickets)
        total_duration = sum(u.get("total_duration_minutes", 0) for u in stats.values())
        total_ticket_count = sum(u.get("ticket_count", 0) for u in stats.values())
        avg_duration = total_duration / total_ticket_count if total_ticket_count > 0 else 0

        all_stars = [s for u in stats.values() for s in u.get("stars", [0,0,0,0,0])]
        total_reviews = sum(all_stars)
        if total_reviews > 0:
            avg_rating = sum((i+1)*s for i,s in enumerate(all_stars)) / total_reviews
        else:
            avg_rating = 0

        embed = discord.Embed(
            title="📊 Support System Statistiken",
            color=discord.Color.gold()
        )
        embed.add_field(
            name="Gesamtübersicht",
            value=(
                f"🎫 Erstellt: **{total_created}**\n"
                f"🔒 Geschlossen: **{total_closed}**\n"
                f"📂 Offen: **{total_open}**\n"
                f"⏱️ Ø Bearbeitungszeit: **{avg_duration:.1f} Min**\n"
                f"⭐ Ø Bewertung: **{avg_rating:.2f} / 5**"
            ),
            inline=False
        )

        # Leaderboard
        if stats:
            sorted_stats = sorted(stats.items(), key=lambda x: x[1].get("closed", 0), reverse=True)
            desc = ""
            for i, (uid, data) in enumerate(sorted_stats[:10], 1):
                user = ctx.guild.get_member(int(uid)) or self.bot.get_user(int(uid))
                name = user.display_name if user else f"ID: {uid}"
                stars = data.get("stars", [0,0,0,0,0])
                reviews = sum(stars)
                avg_user_rating = (sum((i+1)*s for i, s in enumerate(stars)) / reviews) if reviews > 0 else 0
                user_duration = data.get("total_duration_minutes", 0)
                user_ticket_count = data.get("ticket_count", 0)
                avg_user_duration = user_duration / user_ticket_count if user_ticket_count > 0 else 0
                desc += (
                    f"**{i}. {name}**\n"
                    f"🎫 Übernommen: `{data.get('claimed', 0)}` | 🔒 Geschlossen: `{data.get('closed', 0)}`\n"
                    f"⏱️ Ø Dauer: `{avg_user_duration:.1f} Min` | ⭐ Ø `{avg_user_rating:.1f}/5` ({reviews} Reviews)\n\n"
                )
            embed.add_field(name="🏆 Top Support Team", value=desc, inline=False)

        # Kategorie-Statistiken (falls aktiviert)
        if await self.config.guild(ctx.guild).show_category_stats() and category_stats:
            cat_desc = ""
            max_created = max([cs.get("created", 0) for cs in category_stats.values()] or [1])
            for cat_id, cs in category_stats.items():
                cat_name = categories.get(cat_id, {}).get("name", "Unbekannt")
                created = cs.get("created", 0)
                closed = cs.get("closed", 0)
                # Balken
                if await self.config.guild(ctx.guild).use_emoji_charts():
                    bar = self._emoji_bar(created, max_created, 10)
                    cat_desc += f"**{cat_name}**: {created} erstellt, {closed} geschlossen {bar}\n"
                else:
                    cat_desc += f"**{cat_name}**: {created} erstellt, {closed} geschlossen\n"
            embed.add_field(name="📁 Kategorie-Übersicht", value=cat_desc, inline=False)

        await ctx.send(embed=embed)

    @ticket_cmd.command(name="export")
    async def ticket_export(self, ctx: commands.Context):
        """Exportiert alle Statistiken als CSV-Datei."""
        stats = await self.config.guild(ctx.guild).stats()
        category_stats = await self.config.guild(ctx.guild).category_stats()
        categories = await self.config.guild(ctx.guild).categories()

        # Team-Statistiken CSV
        team_csv = io.StringIO()
        team_csv.write("UserID,Name,Claimed,Closed,Reviews,AvgRating,AvgDuration\n")
        for uid, data in stats.items():
            user = ctx.guild.get_member(int(uid)) or self.bot.get_user(int(uid))
            name = user.display_name if user else "Unknown"
            reviews = sum(data.get("stars", [0,0,0,0,0]))
            avg_rating = (sum((i+1)*s for i,s in enumerate(data.get("stars", [0,0,0,0,0]))) / reviews) if reviews > 0 else 0
            ticket_count = data.get("ticket_count", 0)
            avg_duration = data.get("total_duration_minutes", 0) / ticket_count if ticket_count > 0 else 0
            team_csv.write(f"{uid},{name},{data.get('claimed',0)},{data.get('closed',0)},{reviews},{avg_rating:.2f},{avg_duration:.2f}\n")
        team_csv.seek(0)

        # Kategorie-Statistiken CSV
        cat_csv = io.StringIO()
        cat_csv.write("CategoryID,Name,Created,Closed,Reviews,AvgRating,AvgDuration\n")
        for cat_id, cs in category_stats.items():
            cat_name = categories.get(cat_id, {}).get("name", "Unbekannt")
            reviews = sum(cs.get("stars", [0,0,0,0,0]))
            avg_rating = (sum((i+1)*s for i,s in enumerate(cs.get("stars", [0,0,0,0,0]))) / reviews) if reviews > 0 else 0
            ticket_count = cs.get("ticket_count", 0)
            avg_duration = cs.get("total_duration_minutes", 0) / ticket_count if ticket_count > 0 else 0
            cat_csv.write(f"{cat_id},{cat_name},{cs.get('created',0)},{cs.get('closed',0)},{reviews},{avg_rating:.2f},{avg_duration:.2f}\n")
        cat_csv.seek(0)

        # Kombinierte CSV mit beiden Abschnitten
        combined = io.StringIO()
        combined.write("=== Team-Statistiken ===\n")
        combined.write(team_csv.read())
        combined.write("\n=== Kategorie-Statistiken ===\n")
        combined.write(cat_csv.read())
        combined.seek(0)

        file = discord.File(combined, filename="ticket_stats.csv")
        await ctx.send(file=file)
        combined.close()
        team_csv.close()
        cat_csv.close()

    @commands.command(name="tadd")
    @commands.guild_only()
    async def ticket_add(self, ctx: commands.Context, user: discord.Member):
        tickets = await self.config.guild(ctx.guild).active_tickets()
        t_data = next((t for t in tickets if t["channel_id"] == ctx.channel.id), None)
        if not t_data:
            return

        if not await self.is_support(ctx.author, ctx.guild, t_data):
            return await ctx.send("❌ Keine Berechtigung.", delete_after=5)

        try:
            if isinstance(ctx.channel, discord.Thread):
                await ctx.channel.add_user(user)
            else:
                await ctx.channel.set_permissions(user, view_channel=True, send_messages=True, read_message_history=True)
            await ctx.send(f"✅ {user.mention} hinzugefügt.")
        except Exception as e:
            await ctx.send(f"❌ Fehler: {e}")

    @commands.command(name="tremove")
    @commands.guild_only()
    async def ticket_remove(self, ctx: commands.Context, user: discord.Member):
        tickets = await self.config.guild(ctx.guild).active_tickets()
        t_data = next((t for t in tickets if t["channel_id"] == ctx.channel.id), None)
        if not t_data:
            return

        if t_data["user_id"] == user.id:
            return await ctx.send("❌ Du kannst den Ersteller nicht entfernen.")

        if not await self.is_support(ctx.author, ctx.guild, t_data):
            return await ctx.send("❌ Keine Berechtigung.", delete_after=5)

        try:
            if isinstance(ctx.channel, discord.Thread):
                await ctx.channel.remove_user(user)
            else:
                await ctx.channel.set_permissions(user, overwrite=None)
            await ctx.send(f"✅ {user.mention} entfernt.")
        except Exception as e:
            await ctx.send(f"❌ Fehler: {e}")

    @commands.command(name="trename")
    @commands.guild_only()
    async def ticket_rename(self, ctx: commands.Context, *, new_name: str):
        tickets = await self.config.guild(ctx.guild).active_tickets()
        t_data = next((t for t in tickets if t["channel_id"] == ctx.channel.id), None)
        if not t_data:
            return

        if not await self.is_support(ctx.author, ctx.guild, t_data):
            return await ctx.send("❌ Keine Berechtigung.", delete_after=5)

        try:
            await ctx.channel.edit(name=new_name[:100])
            await ctx.send(f"✅ Umbenannt in `{new_name[:100]}`.")
        except Exception as e:
            await ctx.send(f"❌ Fehler: {e}")

    async def is_support(self, member: discord.Member, guild: discord.Guild, ticket_data: dict) -> bool:
        """Prüft, ob ein Mitglied Support-Rechte für das Ticket hat."""
        if member.guild_permissions.manage_guild:
            return True
        cat_data = (await self.config.guild(guild).categories()).get(ticket_data["cat_id"], {})
        allowed_roles = [cat_data.get("staff_role_id"), cat_data.get("high_team_role_id")]
        return any(role_id is not None and role_id in [r.id for r in member.roles] for role_id in allowed_roles)

    # --- Core Logic ---
    async def create_panel(self, channel: discord.TextChannel):
        guild = channel.guild
        categories = await self.config.guild(guild).categories()
        active_tickets = await self.config.guild(guild).active_tickets()

        if len(categories) > 25:
            await channel.send("⚠️ Hinweis: Es sind mehr als 25 Kategorien konfiguriert. Nur die ersten 25 werden im Select angezeigt.")

        embed = discord.Embed(
            title="🎫 Support Ticket System",
            description=(
                "Brauchst du Hilfe? Wähle unten im Dropdown-Menü die passende Kategorie aus.\n\n"
                "⚠️ **Wichtig:** Sobald ein Ticket geschlossen wird, wird der Chatverlauf als HTML-Transkript gespeichert."
            ),
            color=discord.Color.blurple()
        )
        embed.set_footer(text=f"{guild.name} Support Team")

        view = TicketPanelView(self)
        if not categories:
            view.clear_items()
            embed.add_field(
                name="⚠️ Hinweis",
                value="Es wurden noch keine Kategorien erstellt. Ein Admin muss `[p]ticket addcat` nutzen."
            )
        else:
            options = []
            for cat_id, c in categories.items():
                active_count = sum(
                    1 for t in active_tickets
                    if t["cat_id"] == cat_id and t.get("status") == "ACTIVE"
                )
                max_t = c.get("max_tickets", 10)
                label = c["name"][:100]
                if max_t > 0:
                    label = f"{c['name']} ({active_count}/{max_t})"[:100]
                    description = f"{c.get('description', '')} - {int((active_count/max_t)*100)}% ausgelastet"[:100] if c.get('description') else f"{int((active_count/max_t)*100)}% ausgelastet"
                else:
                    description = c.get("description", "")[:100] if c.get("description") else None

                options.append(
                    discord.SelectOption(
                        label=label,
                        value=cat_id,
                        description=description,
                        emoji=c.get("emoji")
                    )
                )
            options = options[:25]
            for child in view.children:
                if isinstance(child, discord.ui.StringSelect):
                    child.options = options

        msg = await channel.send(embed=embed, view=view)
        panels = await self.config.guild(guild).panels()
        panels.append({"channel_id": channel.id, "msg_id": msg.id})
        await self.config.guild(guild).panels.set(panels)

    async def update_panels(self, guild: discord.Guild):
        categories = await self.config.guild(guild).categories()
        active_tickets = await self.config.guild(guild).active_tickets()
        panels = await self.config.guild(guild).panels()
        valid_panels = []

        for p in panels:
            ch = guild.get_channel(p["channel_id"])
            if not ch:
                continue
            try:
                msg = await ch.fetch_message(p["msg_id"])

                options = []
                for cat_id, c in categories.items():
                    active_count = sum(
                        1 for t in active_tickets
                        if t["cat_id"] == cat_id and t.get("status") == "ACTIVE"
                    )
                    max_t = c.get("max_tickets", 10)
                    label = c["name"][:100]
                    if max_t > 0:
                        label = f"{c['name']} ({active_count}/{max_t})"[:100]
                        description = f"{c.get('description', '')} - {int((active_count/max_t)*100)}% ausgelastet"[:100] if c.get('description') else f"{int((active_count/max_t)*100)}% ausgelastet"
                    else:
                        description = c.get("description", "")[:100] if c.get("description") else None

                    options.append(
                        discord.SelectOption(
                            label=label,
                            value=cat_id,
                            description=description,
                            emoji=c.get("emoji")
                        )
                    )
                options = options[:25]

                view = TicketPanelView(self)
                if not options:
                    view.clear_items()
                else:
                    for child in view.children:
                        if isinstance(child, discord.ui.StringSelect):
                            child.options = options

                await msg.edit(view=view)
                # Wichtig: Neue View registrieren, damit Interaktionen funktionieren
                self.bot.add_view(view, message_id=msg.id)
                valid_panels.append(p)
            except discord.NotFound:
                pass
            except Exception as e:
                log.error(f"Failed to update panel {p}: {e}")
                valid_panels.append(p)

        await self.config.guild(guild).panels.set(valid_panels)

    async def finish_base_setup(self, interaction: discord.Interaction, wizard: BaseSetupView):
        guild = interaction.guild
        await self.config.guild(guild).log_channel_id.set(wizard.log_channel_id)
        await self.config.guild(guild).dm_notifications.set(wizard.dm_notifications)
        await self.config.guild(guild).autoclose_hours.set(wizard.autoclose_hours)
        await self.config.guild(guild).cooldown_minutes.set(wizard.cooldown_minutes)
        await self.config.guild(guild).max_tickets_per_user.set(wizard.max_tickets_per_user)
        await self.config.guild(guild).delete_threads_after_close.set(wizard.delete_threads_after_close)
        await self.config.guild(guild).auto_escalate_hours.set(wizard.auto_escalate_hours)
        await self.config.guild(guild).show_category_stats.set(wizard.show_category_stats)
        await self.config.guild(guild).use_emoji_charts.set(wizard.use_emoji_charts)

        await interaction.response.edit_message(
            content="✅ Basis-Setup abgeschlossen!\n\nNutze `[p]ticket postpanel #channel` um das Ticket-Panel zu posten.",
            embed=None,
            view=None
        )

    async def save_category(self, interaction: discord.Interaction, wizard: CategorySetupView, cat_id: str = None):
        guild = interaction.guild
        if not cat_id:
            cat_id = str(uuid.uuid4())[:8]
            action = "gespeichert"
        else:
            action = "aktualisiert"

        categories = await self.config.guild(guild).categories()
        categories[cat_id] = {
            "name": wizard.name,
            "description": wizard.description,
            "emoji": wizard.emoji,
            "abbr": wizard.abbr.upper(),
            "discord_category_id": wizard.discord_category_id,
            "thread_parent_id": wizard.thread_parent_id,
            "staff_role_id": wizard.staff_role_id,
            "high_team_role_id": wizard.high_team_role_id,
            "max_tickets": wizard.max_tickets
        }
        await self.config.guild(guild).categories.set(categories)
        await self.update_panels(guild)

        await interaction.response.edit_message(
            content=f"✅ Kategorie '{wizard.name}' {action}! Alle Panels wurden aktualisiert.",
            embed=None,
            view=None
        )

    async def add_role_to_thread_silently(self, thread: discord.Thread, role: discord.Role):
        for member in role.members:
            try:
                await thread.add_user(member)
            except Exception:
                pass

    async def create_ticket(self, interaction: discord.Interaction, cat_id: str, issue: str):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        guild = interaction.guild
        user = interaction.user
        config = await self.config.guild(guild).all()
        cat_data = config["categories"].get(cat_id)
        if not cat_data:
            return await interaction.followup.send(
                "❌ Ein interner Fehler ist aufgetreten. Das Team wurde benachrichtigt.",
                ephemeral=True
            )

        # Überlastungsprüfung
        max_tickets_cat = cat_data.get("max_tickets", 10)
        if max_tickets_cat > 0:
            active_count = sum(
                1 for t in config["active_tickets"]
                if t["cat_id"] == cat_id and t.get("status") == "ACTIVE"
            )
            if active_count >= max_tickets_cat:
                return await interaction.followup.send(
                    "❌ Diese Kategorie ist aktuell ausgelastet. Bitte warte einen Moment.",
                    ephemeral=True
                )

        staff_role = guild.get_role(cat_data["staff_role_id"])
        high_role = guild.get_role(cat_data.get("high_team_role_id"))

        channel_name = f"{cat_data['abbr']}-{user.name}-{uuid.uuid4().hex[:4]}"[:100]
        ticket_channel = None

        try:
            if cat_data.get("thread_parent_id"):
                parent_ch = guild.get_channel(cat_data["thread_parent_id"])
                if not parent_ch:
                    raise ValueError("Thread-Channel wurde gelöscht.")

                ticket_channel = await parent_ch.create_thread(
                    name=channel_name,
                    type=discord.ChannelType.private_thread,
                    reason=f"Ticket von {user}"
                )
                await ticket_channel.add_user(user)
                if staff_role:
                    await self.add_role_to_thread_silently(ticket_channel, staff_role)
                if high_role:
                    await self.add_role_to_thread_silently(ticket_channel, high_role)

            elif cat_data.get("discord_category_id"):
                category = guild.get_channel(cat_data["discord_category_id"])
                if not category:
                    raise ValueError("Discord-Kategorie wurde gelöscht.")

                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    user: discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                        attach_files=True
                    ),
                    guild.me: discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        manage_channels=True,
                        read_message_history=True
                    ),
                }
                if staff_role:
                    overwrites[staff_role] = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                        manage_messages=True
                    )

                ticket_channel = await guild.create_text_channel(
                    name=channel_name,
                    category=category,
                    overwrites=overwrites,
                    topic=f"Ticket von {user} (ID: {user.id}) | Kat: {cat_data['name']}",
                    reason=f"Ticket erstellt von {user}"
                )
            else:
                raise ValueError("Kategorie hat weder Thread-Channel noch Discord-Kategorie gesetzt.")

        except Exception as e:
            await self.send_log(
                guild,
                "❌ Fehler bei Ticketerstellung",
                discord.Color.red(),
                [
                    ("User", user.mention),
                    ("Kategorie", cat_data.get('name', 'Unbekannt')),
                    ("Fehler", str(e))
                ]
            )
            return await interaction.followup.send(
                "❌ Ein interner Fehler ist aufgetreten. Das Team wurde benachrichtigt.",
                ephemeral=True
            )

        now_iso = datetime.datetime.now().isoformat()
        ticket_data = {
            "channel_id": ticket_channel.id,
            "user_id": user.id,
            "cat_id": cat_id,
            "last_message": now_iso,
            "created_at": now_iso,
            "claimed_by": None,
            "escalated": False,
            "status": "ACTIVE",
            "warned": False,
            "panel_msg_id": None
        }

        embed = discord.Embed(
            title=f"{cat_data['emoji']} Willkommen in deinem Ticket",
            description=(
                f"Hallo {user.mention},\n\n"
                f"ein Teammitglied wird sich gleich um dein Anliegen kümmern.\n\n"
                f"**Dein Anliegen:**\n> {issue}\n\n"
                f"ℹ️ **Hinweis:**\n"
                f"Sobald dieses Ticket geschlossen wird, wird der Chatverlauf als HTML-Datei gespeichert. "
                f"Du hast danach die Möglichkeit, den Support zu bewerten."
            ),
            color=discord.Color.green(),
            timestamp=datetime.datetime.now()
        )
        embed.set_footer(text=f"Ticket-ID: {ticket_channel.id} | Kategorie: {cat_data['name']}")

        mention_staff = staff_role.mention if staff_role else ""
        view = TicketControlView(self)
        msg = await ticket_channel.send(content=f"{user.mention} {mention_staff}", embed=embed, view=view)

        ticket_data["panel_msg_id"] = msg.id

        tickets = config["active_tickets"]
        tickets.append(ticket_data)
        await self.config.guild(guild).active_tickets.set(tickets)

        # Gesamtzähler erhöhen
        current_total = await self.config.guild(guild).total_tickets_created()
        await self.config.guild(guild).total_tickets_created.set(current_total + 1)

        # Kategorie-Statistik "created" erhöhen
        category_stats = config.get("category_stats", {})
        cat_stat = category_stats.get(cat_id, {"created": 0, "closed": 0, "stars": [0,0,0,0,0], "total_duration_minutes": 0, "ticket_count": 0})
        cat_stat["created"] += 1
        category_stats[cat_id] = cat_stat
        await self.config.guild(guild).category_stats.set(category_stats)

        # Cache aktualisieren
        self._add_to_active_cache(guild.id, ticket_channel.id)

        if config["dm_notifications"]:
            await self.send_dm(
                user,
                "🎫 Ticket erstellt",
                f"Dein Ticket auf **{guild.name}** wurde erfolgreich eröffnet.\n"
                f"**Kategorie:** {cat_data['name']}\n"
                f"Ein Teammitglied wird sich bald um dein Anliegen kümmern."
            )

        await self.send_log(
            guild,
            "✅ Ticket eröffnet",
            discord.Color.green(),
            [
                ("Ersteller", f"{user.mention} (`{user.id}`)"),
                ("Kategorie", cat_data['name']),
                ("Channel", ticket_channel.mention)
            ]
        )

        # Panels aktualisieren, um Auslastung anzuzeigen
        await self.update_panels(guild)

        await interaction.followup.send(
            f"✅ Dein Ticket wurde erstellt: {ticket_channel.mention}",
            ephemeral=True
        )

    async def claim_ticket(self, interaction: discord.Interaction, view: TicketControlView):
        channel = interaction.channel
        guild = interaction.guild
        tickets = await self.config.guild(guild).active_tickets()
        ticket_data = next((t for t in tickets if t["channel_id"] == channel.id), None)
        if not ticket_data:
            return await interaction.response.send_message("❌ Kein aktives Ticket.", ephemeral=True)

        if not await self.is_support(interaction.user, guild, ticket_data):
            return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)

        stats = await self.config.guild(guild).stats()
        if ticket_data["claimed_by"] is None:
            ticket_data["claimed_by"] = interaction.user.id
            user_stat = stats.get(str(interaction.user.id), {"claimed": 0, "closed": 0, "stars": [0, 0, 0, 0, 0], "total_duration_minutes": 0, "ticket_count": 0})
            user_stat["claimed"] += 1
            stats[str(interaction.user.id)] = user_stat
            await self.config.guild(guild).stats.set(stats)

            for child in view.children:
                if child.custom_id == "support_ticket_claim_btn":
                    child.label = "Freigeben"
                    child.style = discord.ButtonStyle.secondary
            await interaction.response.edit_message(view=view)
            await channel.send(f"✅ {interaction.user.mention} hat das Ticket übernommen.")
            await self.send_log(
                guild,
                "✋ Ticket übernommen",
                discord.Color.orange(),
                [("Ticket", channel.mention), ("Von", interaction.user.mention)]
            )
        else:
            if ticket_data["claimed_by"] != interaction.user.id:
                return await interaction.response.send_message(
                    "❌ Nur die Person, die übernommen hat, kann freigeben.",
                    ephemeral=True
                )
            ticket_data["claimed_by"] = None
            for child in view.children:
                if child.custom_id == "support_ticket_claim_btn":
                    child.label = "Übernehmen"
                    child.style = discord.ButtonStyle.success
            await interaction.response.edit_message(view=view)
            await channel.send("✅ Ticket freigegeben.")

        for i, t in enumerate(tickets):
            if t["channel_id"] == channel.id:
                tickets[i] = ticket_data
                break
        await self.config.guild(guild).active_tickets.set(tickets)

    async def change_status(self, interaction: discord.Interaction, new_status: str, view: TicketControlView):
        channel = interaction.channel
        guild = interaction.guild
        tickets = await self.config.guild(guild).active_tickets()
        ticket_data = next((t for t in tickets if t["channel_id"] == channel.id), None)
        if not ticket_data:
            return await interaction.response.send_message("❌ Kein aktives Ticket.", ephemeral=True)

        if not await self.is_support(interaction.user, guild, ticket_data):
            return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)

        user = guild.get_member(ticket_data["user_id"])
        is_thread = isinstance(channel, discord.Thread)
        status_names = {
            "ACTIVE": "🟢 Aktiv",
            "WAITING_USER": "🟡 Wartet auf User",
            "WAITING_TEAM": "🔴 Wartet auf Team",
            "PAUSED": "⏸️ Pausiert"
        }

        if new_status == "PAUSED":
            if not is_thread:
                overwrites = channel.overwrites
                if user:
                    overwrites[user] = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=False,
                        read_message_history=True
                    )
                await channel.edit(overwrites=overwrites)
            await channel.send("⏸️ Ticket pausiert. Auto-Close gestoppt.")
        elif new_status == "WAITING_TEAM":
            await channel.send("🔴 Status: Wartet auf Team. Auto-Close pausiert.")
        elif new_status == "WAITING_USER":
            ticket_data["warned"] = False
            if user:
                await channel.send(f"🟡 Status: Wartet auf User. {user.mention}, bitte antworte bald.")
        else:  # ACTIVE
            if not is_thread:
                overwrites = channel.overwrites
                if user:
                    overwrites[user] = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True
                    )
                await channel.edit(overwrites=overwrites)
            ticket_data["warned"] = False
            await channel.send("🟢 Status: Aktiv.")

        ticket_data["status"] = new_status
        ticket_data["last_message"] = datetime.datetime.now().isoformat()

        for i, t in enumerate(tickets):
            if t["channel_id"] == channel.id:
                tickets[i] = ticket_data
                break
        await self.config.guild(guild).active_tickets.set(tickets)
        await interaction.response.send_message("✅ Status geändert.", ephemeral=True)

        config = await self.config.guild(guild).all()
        if config["dm_notifications"] and user:
            await self.send_dm(
                user,
                "🔄 Ticket Status aktualisiert",
                f"Der Status deines Tickets auf **{guild.name}** wurde aktualisiert zu:\n**{status_names.get(new_status)}**"
            )
        await self.send_log(
            guild,
            "🔄 Status geändert",
            discord.Color.blurple(),
            [
                ("Ticket", channel.mention),
                ("Neuer Status", status_names.get(new_status)),
                ("Geändert von", interaction.user.mention)
            ]
        )

        # Panel aktualisieren, da sich die Auslastung geändert haben könnte
        await self.update_panels(guild)

    async def escalate_ticket(self, interaction: discord.Interaction, view: TicketControlView):
        channel = interaction.channel
        guild = interaction.guild
        config = await self.config.guild(guild).all()
        ticket_data = next((t for t in config["active_tickets"] if t["channel_id"] == channel.id), None)
        if not ticket_data:
            return await interaction.response.send_message("❌ Kein aktives Ticket.", ephemeral=True)
        if ticket_data.get("escalated"):
            return await interaction.response.send_message("❌ Bereits eskaliert.", ephemeral=True)

        cat_data = config["categories"].get(ticket_data["cat_id"], {})
        if not cat_data.get("high_team_role_id"):
            return await interaction.response.send_message("❌ Kein High-Team konfiguriert.", ephemeral=True)

        if not await self.is_support(interaction.user, guild, ticket_data):
            return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)

        high_role = guild.get_role(cat_data["high_team_role_id"])
        staff_role = guild.get_role(cat_data["staff_role_id"])
        is_thread = isinstance(channel, discord.Thread)

        if is_thread:
            if staff_role:
                for m in staff_role.members:
                    try:
                        await channel.remove_user(m)
                    except Exception:
                        pass
        else:
            creator = guild.get_member(ticket_data["user_id"])
            overwrites = channel.overwrites
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(view_channel=False)
            if high_role:
                overwrites[high_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True
                )
            if creator:
                overwrites[creator] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True
                )
            await channel.edit(overwrites=overwrites)

        for child in view.children:
            if child.custom_id == "support_ticket_escalate_btn":
                child.disabled = True
        await interaction.response.edit_message(view=view)

        ticket_data["escalated"] = True
        ticket_data["claimed_by"] = None
        await channel.send(
            f"⚠️ **Ticket eskaliert!** {interaction.user.mention} hat das High-Team ({high_role.mention}) hinzugezogen."
        )

        user_obj = guild.get_member(ticket_data["user_id"])
        if config["dm_notifications"] and user_obj:
            await self.send_dm(
                user_obj,
                "⚠️ Ticket eskaliert",
                f"Dein Ticket auf **{guild.name}** wurde an das High-Team eskaliert.\n"
                f"Das High-Team kümmert sich nun um dein Anliegen."
            )
        await self.send_log(
            guild,
            "⚠️ Ticket eskaliert",
            discord.Color.red(),
            [("Ticket", channel.mention), ("Von", interaction.user.mention)]
        )

        for i, t in enumerate(config["active_tickets"]):
            if t["channel_id"] == channel.id:
                config["active_tickets"][i] = ticket_data
                break
        await self.config.guild(guild).active_tickets.set(config["active_tickets"])

        await self.update_panels(guild)

    async def auto_escalate_ticket(self, guild: discord.Guild, ticket_data: dict, channel):
        """Automatische Eskalation bei Inaktivität des Teams (Status WAITING_TEAM)."""
        config = await self.config.guild(guild).all()
        cat_data = config["categories"].get(ticket_data["cat_id"], {})
        if not cat_data.get("high_team_role_id"):
            return

        high_role = guild.get_role(cat_data["high_team_role_id"])
        staff_role = guild.get_role(cat_data["staff_role_id"])
        is_thread = isinstance(channel, discord.Thread)

        if is_thread:
            if staff_role:
                for m in staff_role.members:
                    try:
                        await channel.remove_user(m)
                    except Exception:
                        pass
            if high_role:
                await self.add_role_to_thread_silently(channel, high_role)
        else:
            creator = guild.get_member(ticket_data["user_id"])
            overwrites = channel.overwrites
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(view_channel=False)
            if high_role:
                overwrites[high_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True
                )
            if creator:
                overwrites[creator] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True
                )
            await channel.edit(overwrites=overwrites)

        # View aktualisieren: Eskalations-Button deaktivieren
        if ticket_data.get("panel_msg_id"):
            try:
                msg = await channel.fetch_message(ticket_data["panel_msg_id"])
                view = TicketControlView(self)
                for child in view.children:
                    if child.custom_id == "support_ticket_escalate_btn":
                        child.disabled = True
                    if child.custom_id == "support_ticket_claim_btn" and ticket_data.get("claimed_by"):
                        child.label = "Freigeben"
                        child.style = discord.ButtonStyle.secondary
                await msg.edit(view=view)
                self.bot.add_view(view, message_id=msg.id)
            except Exception as e:
                log.error(f"Failed to update view after auto-escalation: {e}")

        ticket_data["escalated"] = True
        ticket_data["claimed_by"] = None

        await channel.send(
            f"⚠️ **Automatische Eskalation:** Dieses Ticket wurde automatisch an das High-Team ({high_role.mention}) eskaliert, da es zu lange auf 'Wartet auf Team' stand."
        )

        user_obj = guild.get_member(ticket_data["user_id"])
        if config["dm_notifications"] and user_obj:
            await self.send_dm(
                user_obj,
                "⚠️ Ticket automatisch eskaliert",
                f"Dein Ticket auf **{guild.name}** wurde automatisch an das High-Team eskaliert."
            )
        await self.send_log(
            guild,
            "🚨 Automatische Eskalation",
            discord.Color.red(),
            [("Ticket", channel.mention), ("Grund", "Team-Inaktivität")]
        )

        # WICHTIG: Keine Config-Speicherung hier, die Schleife übernimmt das.

    async def close_ticket(
        self,
        channel: discord.TextChannel,
        reason: str,
        user: discord.Member,
        interaction: discord.Interaction = None,
        is_auto: bool = False
    ):
        guild = channel.guild
        config = await self.config.guild(guild).all()
        ticket_data = next((t for t in config["active_tickets"] if t["channel_id"] == channel.id), None)
        if not ticket_data:
            return

        if interaction and interaction.message:
            try:
                view = TicketControlView(self)
                for child in view.children:
                    child.disabled = True
                await interaction.message.edit(view=view)
            except Exception:
                pass

        # Statistiken aktualisieren: closed + Dauer dem Bearbeiter (Claimer) oder Schließenden gutschreiben
        if not is_auto:
            stats = config.get("stats", {})
            closed_by_id = ticket_data.get("claimed_by") or user.id
            user_stat = stats.get(str(closed_by_id), {"claimed": 0, "closed": 0, "stars": [0, 0, 0, 0, 0], "total_duration_minutes": 0, "ticket_count": 0})
            user_stat["closed"] += 1

            created_at = datetime.datetime.fromisoformat(ticket_data["created_at"])
            closed_at = datetime.datetime.now()
            duration_min = (closed_at - created_at).total_seconds() / 60
            user_stat["total_duration_minutes"] += duration_min
            user_stat["ticket_count"] += 1

            stats[str(closed_by_id)] = user_stat
            await self.config.guild(guild).stats.set(stats)

            # Kategorie-Statistik aktualisieren
            cat_stats = config.get("category_stats", {})
            cat_id = ticket_data["cat_id"]
            cat_stat = cat_stats.get(cat_id, {"created": 0, "closed": 0, "stars": [0,0,0,0,0], "total_duration_minutes": 0, "ticket_count": 0})
            cat_stat["closed"] += 1
            cat_stat["total_duration_minutes"] += duration_min
            cat_stat["ticket_count"] += 1
            cat_stats[cat_id] = cat_stat
            await self.config.guild(guild).category_stats.set(cat_stats)

        # HTML-Transkript erstellen
        messages_html = ""
        try:
            async for message in channel.history(limit=None, oldest_first=True):
                content = discord.utils.escape_html(message.content) if message.content else "[Kein Text / Nur Anhang/Embed]"
                if message.attachments:
                    content += f"<br><i>[Anhänge: {', '.join([a.url for a in message.attachments])}]</i>"
                user_color = "#ffffff" if not message.author.color or message.author.color.value == 0 else str(message.author.color)
                messages_html += MESSAGE_HTML.format(
                    avatar_url=message.author.display_avatar.url,
                    author=message.author.display_name,
                    color=user_color,
                    timestamp=message.created_at.strftime("%d.%m.%Y %H:%M"),
                    content=content
                )
        except Exception:
            pass

        html_content = HTML_TEMPLATE.format(
            channel_name=channel.name,
            created_at=channel.created_at.strftime("%d.%m.%Y %H:%M"),
            closed_at=datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
            close_reason=discord.utils.escape_html(reason),
            messages_html=messages_html
        )
        transcript_file = discord.File(io.StringIO(html_content), filename=f"transcript-{channel.id}.html")

        log_channel = guild.get_channel(config.get("log_channel_id"))
        if log_channel:
            log_embed = discord.Embed(
                title="Ticket geschlossen" + (" (Auto-Close)" if is_auto else ""),
                color=discord.Color.red(),
                timestamp=datetime.datetime.now()
            )
            log_embed.add_field(name="Geschlossen von", value="System" if is_auto else f"{user.mention} (`{user.id}`)")
            log_embed.add_field(name="Channel", value=channel.name)
            log_embed.add_field(name="Grund", value=reason, inline=False)
            try:
                await log_channel.send(embed=log_embed, file=transcript_file)
            except Exception as e:
                log.error(f"Failed to send transcript to log channel: {e}")

        user_obj = guild.get_member(ticket_data["user_id"]) or self.bot.get_user(ticket_data["user_id"])
        if config["dm_notifications"] and user_obj:
            try:
                await user_obj.send(
                    embed=discord.Embed(
                        title="🎫 Ticket geschlossen",
                        description=(
                            f"Dein Ticket auf **{guild.name}** wurde geschlossen.\n"
                            f"**Grund:** {reason}\n"
                            f"Im Anhang findest du den Chatverlauf."
                        ),
                        color=discord.Color.blurple()
                    ),
                    file=discord.File(io.StringIO(html_content), filename=f"transcript-{channel.id}.html")
                )
            except Exception:
                pass

        # Review-View senden oder direkt archivieren/löschen
        if is_auto:
            await self.delete_ticket_channel(channel, ticket_data, 0)
        else:
            msg = await channel.send(
                embed=discord.Embed(
                    title="⭐ Support Bewerten",
                    description="Wie würdest du den Support bewerten?",
                    color=discord.Color.gold()
                ),
                view=ReviewView(self, ticket_data)
            )
            msg.view.message = msg

    async def delete_ticket_channel(self, channel: discord.TextChannel, ticket_data: dict, stars: int):
        guild = channel.guild
        config = await self.config.guild(guild).all()

        # Prüfen, ob Ticket noch existiert (könnte bereits entfernt worden sein)
        if not any(t["channel_id"] == channel.id for t in config["active_tickets"]):
            return

        if stars > 0 and ticket_data.get("claimed_by"):
            stats = config.get("stats", {})
            claimer_id = str(ticket_data["claimed_by"])
            user_stat = stats.get(claimer_id, {"claimed": 0, "closed": 0, "stars": [0, 0, 0, 0, 0], "total_duration_minutes": 0, "ticket_count": 0})
            if len(user_stat["stars"]) == 5:
                user_stat["stars"][stars - 1] += 1
            stats[claimer_id] = user_stat
            await self.config.guild(guild).stats.set(stats)

            # Kategorie-Statistik Bewertung hinzufügen
            cat_id = ticket_data["cat_id"]
            cat_stats = config.get("category_stats", {})
            cat_stat = cat_stats.get(cat_id, {"created": 0, "closed": 0, "stars": [0,0,0,0,0], "total_duration_minutes": 0, "ticket_count": 0})
            if len(cat_stat["stars"]) == 5:
                cat_stat["stars"][stars - 1] += 1
            cat_stats[cat_id] = cat_stat
            await self.config.guild(guild).category_stats.set(cat_stats)

            if config.get("log_channel_id"):
                log_channel = guild.get_channel(config["log_channel_id"])
                if log_channel:
                    user_obj = guild.get_member(ticket_data["user_id"]) or self.bot.get_user(ticket_data["user_id"])
                    cat_data = config["categories"].get(ticket_data["cat_id"], {})
                    rev_embed = discord.Embed(
                        title="⭐ Neues Ticket-Review",
                        color=discord.Color.gold()
                    )
                    rev_embed.add_field(name="Bewertung", value=f"{'⭐' * stars} ({stars}/5)")
                    rev_embed.add_field(
                        name="User",
                        value=f"{user_obj.mention}" if user_obj else "Unbekannt"
                    )
                    rev_embed.add_field(name="Kategorie", value=cat_data.get("name", "Unbekannt"))
                    try:
                        await log_channel.send(embed=rev_embed)
                    except Exception:
                        pass

        tickets = config["active_tickets"]
        tickets = [t for t in tickets if t["channel_id"] != channel.id]
        await self.config.guild(guild).active_tickets.set(tickets)

        # Cache aktualisieren
        self._remove_from_active_cache(guild.id, channel.id)

        # Thread löschen oder archivieren, je nach Einstellung
        try:
            if isinstance(channel, discord.Thread):
                delete_threads = config.get("delete_threads_after_close", False)
                if delete_threads:
                    await channel.delete()
                else:
                    await channel.edit(archived=True, locked=True, reason=f"Ticket geschlossen ({stars}/5)")
            else:
                await channel.delete(reason=f"Ticket geschlossen ({stars}/5)")
        except Exception as e:
            log.error(f"Failed to close ticket channel: {e}")

        # Panels aktualisieren
        await self.update_panels(guild)

    @ticket_cmd.command(name="reset")
    async def ticket_reset(self, ctx: commands.Context):
        """Setzt die Ticket-Konfiguration komplett zurück und schließt alle aktiven Tickets."""
        tickets = await self.config.guild(ctx.guild).active_tickets()
        delete_threads = await self.config.guild(ctx.guild).delete_threads_after_close()
        for t in tickets:
            ch = ctx.guild.get_channel(t["channel_id"])
            if ch:
                try:
                    if isinstance(ch, discord.Thread):
                        if delete_threads:
                            await ch.delete()
                        else:
                            await ch.edit(archived=True, locked=True)
                    else:
                        await ch.delete()
                except Exception:
                    pass

        # Cache leeren
        self._active_channel_cache[ctx.guild.id] = set()

        await self.config.guild(ctx.guild).clear()
        await ctx.send("✅ Die Ticket-Konfiguration wurde komplett zurückgesetzt und alle aktiven Tickets geschlossen.")


async def setup(bot):
    await bot.add_cog(SupportCog(bot))
