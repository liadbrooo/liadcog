"""
SupportCog V48 - Final Stable Release
- Alle Funktionen: Setup, Wizard, Kategorien, Panel, Stats, Verlauf, Zusammenfassungen,
  Reaktionszeit, Export, Rollen-Sync, Admin-Rolle, High-Team automatisch.
- Optimierte Ticket-Erstellung (nur konfigurierte Rollen, paralleles Hinzufügen)
- Stabile Config-Zugriffe, korrekte Berechtigungsprüfungen
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
                "Bitte warte noch einen Moment...", ephemeral=True)

        config = await self.cog.config.guild(interaction.guild).all()
        cat_id = select.values[0]
        if cat_id not in config.get("categories", {}):
            return await interaction.response.send_message("❌ Diese Kategorie existiert nicht mehr.", ephemeral=True)

        if interaction.user.id in config.get("blacklist", []):
            return await interaction.response.send_message("❌ Du bist gesperrt.", ephemeral=True)

        max_tickets = config.get("max_tickets_per_user", 1)
        user_tickets = [t for t in config.get("active_tickets", []) if t["user_id"] == interaction.user.id]
        if len(user_tickets) >= max_tickets:
            return await interaction.response.send_message(
                f"❌ Du hast bereits das Maximum von {max_tickets} offenen Tickets.", ephemeral=True)

        cooldown_mins = config.get("cooldown_minutes", 0)
        if cooldown_mins > 0:
            now = datetime.datetime.now()
            for t in user_tickets:
                created = datetime.datetime.fromisoformat(t["created_at"])
                if (now - created).total_seconds() / 60 < cooldown_mins:
                    return await interaction.response.send_message("❌ Cooldown aktiv.", ephemeral=True)

        cat_data = config["categories"].get(cat_id, {})
        max_cat_tickets = cat_data.get("max_tickets", 10)
        if max_cat_tickets > 0:
            active_count = sum(1 for t in config["active_tickets"] if t["cat_id"] == cat_id and t.get("status") == "ACTIVE")
            if active_count >= max_cat_tickets:
                return await interaction.response.send_message("❌ Kategorie ist ausgelastet.", ephemeral=True)

        await interaction.response.send_modal(TicketModal(self.cog, cat_id))


class CloseTicketModal(discord.ui.Modal, title='🔒 Ticket schließen'):
    def __init__(self, cog: "SupportCog"):
        super().__init__()
        self.cog = cog

    reason = discord.ui.TextInput(
        label='Grund für die Schließung',
        placeholder='Optional',
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
            discord.SelectOption(label="Aktiv", value="ACTIVE", emoji="🟢"),
            discord.SelectOption(label="Wartet auf User", value="WAITING_USER", emoji="🟡"),
            discord.SelectOption(label="Wartet auf Team", value="WAITING_TEAM", emoji="🔴"),
            discord.SelectOption(label="Pausiert", value="PAUSED", emoji="⏸️")
        ]
    )
    async def status_select(self, interaction: discord.Interaction, select: discord.ui.Select):
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
            return await interaction.response.send_message("Nur der Ticket-Ersteller kann bewerten.", ephemeral=True)
        stars = int(interaction.data["custom_id"][-1])
        await interaction.response.edit_message(content=f"Danke für dein Feedback ({stars}⭐)!", view=None)
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
            return await interaction.response.send_message("❌ Bitte eine Zahl eingeben.", ephemeral=True)
        if not (self.min_val <= val <= self.max_val):
            return await interaction.response.send_message(f"❌ Wert muss zwischen {self.min_val} und {self.max_val} liegen.", ephemeral=True)
        setattr(self.wizard, self.attr_name, val)
        self.wizard._update_labels()
        await interaction.response.edit_message(view=self.wizard)


class CategoryEditModal(discord.ui.Modal, title="Kategorie bearbeiten"):
    def __init__(self, cog: "SupportCog", cat_id: str, cat_data: dict):
        super().__init__()
        self.cog = cog
        self.cat_id = cat_id

        self.name_input = discord.ui.TextInput(label="Name", default=cat_data.get("name", ""), max_length=50, required=True)
        self.desc_input = discord.ui.TextInput(label="Beschreibung", default=cat_data.get("description", ""), max_length=100, required=False)
        self.abbr_input = discord.ui.TextInput(label="Abkürzung", default=cat_data.get("abbr", ""), max_length=10, required=True)
        self.emoji_input = discord.ui.TextInput(label="Emoji", default=cat_data.get("emoji", "🎫"), max_length=10, required=False)
        self.max_tickets_input = discord.ui.TextInput(label="Max aktiv", default=str(cat_data.get("max_tickets", 10)), max_length=3, required=True)

        self.add_item(self.name_input)
        self.add_item(self.desc_input)
        self.add_item(self.abbr_input)
        self.add_item(self.emoji_input)
        self.add_item(self.max_tickets_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            max_tickets = int(self.max_tickets_input.value)
        except:
            return await interaction.response.send_message("❌ Max aktiv muss eine Zahl sein.", ephemeral=True)
        if max_tickets < 0 or max_tickets > 100:
            return await interaction.response.send_message("❌ Max aktiv zwischen 0 und 100.", ephemeral=True)

        categories = await self.cog.config.guild(interaction.guild).categories()
        if self.cat_id not in categories:
            return await interaction.response.send_message("❌ Kategorie nicht gefunden.", ephemeral=True)

        categories[self.cat_id].update({
            "name": self.name_input.value,
            "description": self.desc_input.value or None,
            "abbr": self.abbr_input.value,
            "emoji": self.emoji_input.value or "🎫",
            "max_tickets": max_tickets
        })
        await self.cog.config.guild(interaction.guild).categories.set(categories)
        await self.cog.update_panels(interaction.guild)
        await interaction.response.send_message("✅ Kategorie aktualisiert!", ephemeral=True)


class CategoryAllTextModal(discord.ui.Modal, title="Kategorie Texte"):
    def __init__(self, wizard: 'CategorySetupView'):
        super().__init__()
        self.wizard = wizard
        self.name_input = discord.ui.TextInput(label="Name", default=wizard.name or "", max_length=50, required=True)
        self.desc_input = discord.ui.TextInput(label="Beschreibung", default=wizard.description or "", max_length=100, required=False)
        self.abbr_input = discord.ui.TextInput(label="Abkürzung", default=wizard.abbr or "", max_length=10, required=True)
        self.emoji_input = discord.ui.TextInput(label="Emoji", default=wizard.emoji or "🎫", max_length=10, required=False)
        self.add_item(self.name_input)
        self.add_item(self.desc_input)
        self.add_item(self.abbr_input)
        self.add_item(self.emoji_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.wizard.name = self.name_input.value
        self.wizard.description = self.desc_input.value or None
        self.wizard.abbr = self.abbr_input.value
        self.wizard.emoji = self.emoji_input.value or "🎫"
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
        self.btn_texts = discord.ui.Button(label="Texte anpassen", style=discord.ButtonStyle.primary, row=0, emoji="📝")
        self.btn_texts.callback = self._texts_cb
        self.add_item(self.btn_texts)

        self.btn_max_tickets = discord.ui.Button(label=f"Max aktiv: {self.max_tickets}", style=discord.ButtonStyle.secondary, emoji='📊', row=0)
        self.btn_max_tickets.callback = self._max_tickets_cb
        self.add_item(self.btn_max_tickets)

        self.btn_save = discord.ui.Button(label="Speichern", style=discord.ButtonStyle.success, emoji='✅', row=0)
        self.btn_save.callback = self._save_cb
        self.add_item(self.btn_save)

        cat_options = [discord.SelectOption(label=cat.name[:100], value=str(cat.id)) for cat in self.ctx.guild.categories[:25]]
        if not cat_options:
            cat_options = [discord.SelectOption(label="Keine Kategorien", value="none")]
        self.disc_cat_sel = discord.ui.Select(placeholder="Discord Kategorie", options=cat_options, row=1)
        self.disc_cat_sel.callback = self._disc_cat_cb
        self.add_item(self.disc_cat_sel)

        thread_options = [discord.SelectOption(label=f"#{c.name}"[:100], value=str(c.id)) for c in self.ctx.guild.text_channels[:25]]
        if not thread_options:
            thread_options = [discord.SelectOption(label="Keine Textkanäle", value="none")]
        self.thread_sel = discord.ui.Select(placeholder="Thread-Channel", options=thread_options, row=2)
        self.thread_sel.callback = self._thread_cb
        self.add_item(self.thread_sel)

        staff_options = [discord.SelectOption(label=role.name[:100], value=str(role.id)) for role in self.ctx.guild.roles if not role.managed][:25]
        if not staff_options:
            staff_options = [discord.SelectOption(label="Keine Rollen", value="none")]
        self.staff_sel = discord.ui.Select(placeholder="Support-Rolle", options=staff_options, row=3)
        self.staff_sel.callback = self._staff_cb
        self.add_item(self.staff_sel)

        high_options = staff_options.copy()
        self.high_sel = discord.ui.Select(placeholder="High-Team Rolle", options=high_options, row=4)
        self.high_sel.callback = self._high_cb
        self.add_item(self.high_sel)

    def _update_labels(self):
        self.btn_texts.label = f"Name: {self.name}" if self.name else "Texte anpassen"
        self.btn_max_tickets.label = f"Max aktiv: {self.max_tickets}"

    async def _texts_cb(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CategoryAllTextModal(self))

    async def _max_tickets_cb(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SimpleNumberModal(self, "max_tickets", "Maximale aktive Tickets", 0, 100))

    async def _disc_cat_cb(self, interaction: discord.Interaction):
        if self.disc_cat_sel.values[0] != "none":
            self.discord_category_id = int(self.disc_cat_sel.values[0])
            cat = self.ctx.guild.get_channel(self.discord_category_id)
            self.disc_cat_sel.placeholder = f"Kategorie: {cat.name}" if cat else "Discord Kategorie"
        else:
            self.discord_category_id = None
            self.disc_cat_sel.placeholder = "Discord Kategorie"
        await interaction.response.edit_message(view=self)

    async def _thread_cb(self, interaction: discord.Interaction):
        if self.thread_sel.values[0] != "none":
            self.thread_parent_id = int(self.thread_sel.values[0])
            ch = self.ctx.guild.get_channel(self.thread_parent_id)
            self.thread_sel.placeholder = f"Thread: #{ch.name}" if ch else "Thread-Channel"
        else:
            self.thread_parent_id = None
            self.thread_sel.placeholder = "Thread-Channel"
        await interaction.response.edit_message(view=self)

    async def _staff_cb(self, interaction: discord.Interaction):
        if self.staff_sel.values[0] != "none":
            self.staff_role_id = int(self.staff_sel.values[0])
            role = self.ctx.guild.get_role(self.staff_role_id)
            self.staff_sel.placeholder = f"Support: {role.name}" if role else "Support-Rolle"
        else:
            self.staff_role_id = None
            self.staff_sel.placeholder = "Support-Rolle"
        await interaction.response.edit_message(view=self)

    async def _high_cb(self, interaction: discord.Interaction):
        if self.high_sel.values[0] != "none":
            self.high_team_role_id = int(self.high_sel.values[0])
            role = self.ctx.guild.get_role(self.high_team_role_id)
            self.high_sel.placeholder = f"High-Team: {role.name}" if role else "High-Team Rolle"
        else:
            self.high_team_role_id = None
            self.high_sel.placeholder = "High-Team Rolle"
        await interaction.response.edit_message(view=self)

    async def _save_cb(self, interaction: discord.Interaction):
        if not self.name or not self.abbr or not self.staff_role_id:
            return await interaction.response.send_message("❌ Bitte fülle Name, Abkürzung und Support-Rolle aus.", ephemeral=True)
        if not self.discord_category_id and not self.thread_parent_id:
            return await interaction.response.send_message("❌ Bitte wähle Discord-Kategorie oder Thread-Channel.", ephemeral=True)
        await self.cog.save_category(interaction, self, self.cat_id)
        self.stop()


class BaseSetupView(discord.ui.View):
    """Interaktive Basis-Konfiguration."""
    def __init__(self, cog: "SupportCog", ctx: commands.Context):
        super().__init__(timeout=300)
        self.cog = cog
        self.ctx = ctx
        self.log_channel_id = None
        self.admin_role_id = None
        self.dm_notifications = True
        self.autoclose_hours = 48
        self.cooldown_minutes = 0
        self.max_tickets_per_user = 1
        self.delete_threads_after_close = False
        self.auto_escalate_hours = 0
        self.show_category_stats = True
        self.use_emoji_charts = True
        self._build_ui()

    def _build_ui(self):
        log_options = [discord.SelectOption(label=f"#{c.name}"[:100], value=str(c.id)) for c in self.ctx.guild.text_channels[:25]]
        if not log_options:
            log_options = [discord.SelectOption(label="Keine Textkanäle", value="none")]
        self.log_sel = discord.ui.Select(placeholder="Log-Channel wählen", options=log_options, row=0)
        self.log_sel.callback = self._log_select_cb
        self.add_item(self.log_sel)

        # Admin-Rolle (optional)
        admin_options = [discord.SelectOption(label=role.name[:100], value=str(role.id)) for role in self.ctx.guild.roles if not role.managed][:25]
        if not admin_options:
            admin_options = [discord.SelectOption(label="Keine Rollen", value="none")]
        self.admin_sel = discord.ui.Select(placeholder="Admin-Rolle (für private Threads)", options=admin_options, row=1)
        self.admin_sel.callback = self._admin_select_cb
        self.add_item(self.admin_sel)

        self.btn_dm = discord.ui.Button(label="DMs: AN", style=discord.ButtonStyle.success, emoji='✉️', row=2)
        self.btn_dm.callback = self._dm_toggle_cb
        self.add_item(self.btn_dm)

        self.btn_auto = discord.ui.Button(label=f"Auto-Close: {self.autoclose_hours}h", style=discord.ButtonStyle.secondary, emoji='⏳', row=2)
        self.btn_auto.callback = self._auto_cb
        self.add_item(self.btn_auto)

        self.btn_cool = discord.ui.Button(label=f"Cooldown: {self.cooldown_minutes}m", style=discord.ButtonStyle.secondary, emoji='❄️', row=2)
        self.btn_cool.callback = self._cool_cb
        self.add_item(self.btn_cool)

        self.btn_max = discord.ui.Button(label=f"Max Tickets: {self.max_tickets_per_user}", style=discord.ButtonStyle.secondary, emoji='🔢', row=2)
        self.btn_max.callback = self._max_cb
        self.add_item(self.btn_max)

        self.btn_del_thread = discord.ui.Button(label="Threads löschen: AUS", style=discord.ButtonStyle.danger, emoji='🗑️', row=3)
        self.btn_del_thread.callback = self._del_thread_toggle_cb
        self.add_item(self.btn_del_thread)

        self.btn_esc = discord.ui.Button(label=f"Auto-Eskalation: {self.auto_escalate_hours}h", style=discord.ButtonStyle.secondary, emoji='🚨', row=3)
        self.btn_esc.callback = self._esc_cb
        self.add_item(self.btn_esc)

        self.btn_catstats = discord.ui.Button(label="Kategorie-Statistiken: AN", style=discord.ButtonStyle.success, emoji='📊', row=3)
        self.btn_catstats.callback = self._catstats_toggle_cb
        self.add_item(self.btn_catstats)

        self.btn_emoji = discord.ui.Button(label="Emoji-Balken: AN", style=discord.ButtonStyle.success, emoji='📈', row=3)
        self.btn_emoji.callback = self._emoji_toggle_cb
        self.add_item(self.btn_emoji)

        self.btn_finish = discord.ui.Button(label="Setup abschließen", style=discord.ButtonStyle.success, emoji='✅', row=4)
        self.btn_finish.callback = self._finish_cb
        self.add_item(self.btn_finish)

    def _update_labels(self):
        self.btn_dm.label = f"DMs: {'AN' if self.dm_notifications else 'AUS'}"
        self.btn_dm.style = discord.ButtonStyle.success if self.dm_notifications else discord.ButtonStyle.danger
        self.btn_auto.label = f"Auto-Close: {self.autoclose_hours}h"
        self.btn_cool.label = f"Cooldown: {self.cooldown_minutes}m"
        self.btn_max.label = f"Max Tickets: {self.max_tickets_per_user}"
        self.btn_del_thread.label = f"Threads löschen: {'AN' if self.delete_threads_after_close else 'AUS'}"
        self.btn_del_thread.style = discord.ButtonStyle.success if self.delete_threads_after_close else discord.ButtonStyle.danger
        self.btn_esc.label = f"Auto-Eskalation: {self.auto_escalate_hours}h"
        self.btn_catstats.label = f"Kategorie-Statistiken: {'AN' if self.show_category_stats else 'AUS'}"
        self.btn_catstats.style = discord.ButtonStyle.success if self.show_category_stats else discord.ButtonStyle.danger
        self.btn_emoji.label = f"Emoji-Balken: {'AN' if self.use_emoji_charts else 'AUS'}"
        self.btn_emoji.style = discord.ButtonStyle.success if self.use_emoji_charts else discord.ButtonStyle.danger

    async def _log_select_cb(self, interaction: discord.Interaction):
        if self.log_sel.values[0] != "none":
            self.log_channel_id = int(self.log_sel.values[0])
            ch = self.ctx.guild.get_channel(self.log_channel_id)
            self.log_sel.placeholder = f"Log-Channel: #{ch.name}" if ch else "Log-Channel wählen"
        else:
            self.log_sel.placeholder = "Log-Channel wählen"
        await interaction.response.edit_message(view=self)

    async def _admin_select_cb(self, interaction: discord.Interaction):
        if self.admin_sel.values[0] != "none":
            self.admin_role_id = int(self.admin_sel.values[0])
            role = self.ctx.guild.get_role(self.admin_role_id)
            self.admin_sel.placeholder = f"Admin-Rolle: {role.name}" if role else "Admin-Rolle wählen"
        else:
            self.admin_role_id = None
            self.admin_sel.placeholder = "Admin-Rolle (für private Threads)"
        await interaction.response.edit_message(view=self)

    async def _dm_toggle_cb(self, interaction: discord.Interaction):
        self.dm_notifications = not self.dm_notifications
        self._update_labels()
        await interaction.response.edit_message(view=self)

    async def _auto_cb(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SimpleNumberModal(self, "autoclose_hours", "Auto-Close (Stunden)", 0, 500))

    async def _cool_cb(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SimpleNumberModal(self, "cooldown_minutes", "Cooldown (Minuten)", 0, 10080))

    async def _max_cb(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SimpleNumberModal(self, "max_tickets_per_user", "Max Tickets pro User", 1, 10))

    async def _del_thread_toggle_cb(self, interaction: discord.Interaction):
        self.delete_threads_after_close = not self.delete_threads_after_close
        self._update_labels()
        await interaction.response.edit_message(view=self)

    async def _esc_cb(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SimpleNumberModal(self, "auto_escalate_hours", "Auto-Eskalation nach Stunden (0=aus)", 0, 500))

    async def _catstats_toggle_cb(self, interaction: discord.Interaction):
        self.show_category_stats = not self.show_category_stats
        self._update_labels()
        await interaction.response.edit_message(view=self)

    async def _emoji_toggle_cb(self, interaction: discord.Interaction):
        self.use_emoji_charts = not self.use_emoji_charts
        self._update_labels()
        await interaction.response.edit_message(view=self)

    async def _finish_cb(self, interaction: discord.Interaction):
        if not self.log_channel_id:
            return await interaction.response.send_message("Bitte wähle zuerst einen Log-Channel aus!", ephemeral=True)
        await self.cog.finish_base_setup(interaction, self)
        self.stop()


class SupportCog(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=98765432123456789, force_registration=True)
        default_guild = {
            "panels": [],
            "log_channel_id": None,
            "admin_role_id": None,
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
            "category_stats": {},
            "ticket_history": []
        }
        self.config.register_guild(**default_guild)
        self._active_channel_cache = {}
        self.autoclose_task = None
        self.init_task = None
        self.summary_task = None
        self._last_daily_summary = None
        self._last_weekly_summary = None

    async def cog_load(self):
        self.init_task = self.bot.loop.create_task(self._async_init())
        self.summary_task = self.bot.loop.create_task(self.summary_loop())

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
            self._active_channel_cache[guild_id] = {t["channel_id"] for t in data.get("active_tickets", [])}
            for panel in data.get("panels", []):
                try:
                    view = TicketPanelView(self)
                    categories = data.get("categories", {})
                    if categories:
                        options = self._build_panel_options(categories, data.get("active_tickets", []))
                        for child in view.children:
                            if isinstance(child, discord.ui.Select):
                                child.options = options
                    else:
                        view.clear_items()
                    self.bot.add_view(view, message_id=panel["msg_id"])

                    # Embed aktualisieren
                    try:
                        ch = guild.get_channel(panel["channel_id"])
                        if ch:
                            msg = await ch.fetch_message(panel["msg_id"])
                            embed = await self._build_panel_embed(guild)
                            await msg.edit(embed=embed)
                    except:
                        pass
                except Exception as e:
                    log.error(f"Panel-Registrierung fehlgeschlagen: {e}")
            for ticket in data.get("active_tickets", []):
                if ticket.get("panel_msg_id"):
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
                        log.error(f"Control-View Registrierung fehlgeschlagen: {e}")

    def cog_unload(self):
        if self.autoclose_task:
            self.autoclose_task.cancel()
        if self.init_task and not self.init_task.done():
            self.init_task.cancel()
        if self.summary_task:
            self.summary_task.cancel()

    # Cache-Helfer
    def _add_to_active_cache(self, guild_id, channel_id):
        if guild_id not in self._active_channel_cache:
            self._active_channel_cache[guild_id] = set()
        self._active_channel_cache[guild_id].add(channel_id)

    def _remove_from_active_cache(self, guild_id, channel_id):
        if guild_id in self._active_channel_cache:
            self._active_channel_cache[guild_id].discard(channel_id)

    # Helpers
    async def send_dm(self, user, title, description):
        try:
            embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
            await user.send(embed=embed)
        except:
            pass

    async def send_log(self, guild, title, color, fields):
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
            log.error(f"Log senden fehlgeschlagen: {e}")

    def _emoji_bar(self, value, max_value, length=10):
        if max_value <= 0:
            return "⬜" * length
        filled = int((value / max_value) * length)
        filled = max(0, min(filled, length))
        return "🟩" * filled + "⬜" * (length - filled)

    # Rollen zu Thread synchronisieren (optimiert)
    async def _sync_roles_to_thread(self, thread, guild):
        config = await self.config.guild(guild).all()
        categories = config.get("categories", {})
        staff_role_id = None
        high_role_id = None
        for cat in categories.values():
            staff_role_id = cat.get("staff_role_id")
            high_role_id = cat.get("high_team_role_id")
            break

        roles_to_add = []
        if staff_role_id:
            staff_role = guild.get_role(staff_role_id)
            if staff_role:
                roles_to_add.append(staff_role)
        if high_role_id:
            high_role = guild.get_role(high_role_id)
            if high_role:
                roles_to_add.append(high_role)

        # Optionale Admin-Rolle
        admin_role_id = config.get("admin_role_id")
        if admin_role_id:
            admin_role = guild.get_role(admin_role_id)
            if admin_role:
                roles_to_add.append(admin_role)

        # Alle Mitglieder der Rollen parallel hinzufügen
        members_to_add = set()
        for role in roles_to_add:
            members_to_add.update(role.members)

        await asyncio.gather(*[thread.add_user(member) for member in members_to_add], return_exceptions=True)

    # Zusammenfassungs-Loop
    async def summary_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now = datetime.datetime.utcnow()
            if self._last_daily_summary is None or now.date() > self._last_daily_summary:
                self._last_daily_summary = now.date()
                for guild_id in (await self.config.all_guilds()).keys():
                    guild = self.bot.get_guild(guild_id)
                    if guild:
                        await self.send_summary(guild, "daily")
            if self._last_weekly_summary is None or (now.date().weekday() == 0 and now.date() > self._last_weekly_summary):
                self._last_weekly_summary = now.date()
                for guild_id in (await self.config.all_guilds()).keys():
                    guild = self.bot.get_guild(guild_id)
                    if guild:
                        await self.send_summary(guild, "weekly")
            await asyncio.sleep(60)

    async def send_summary(self, guild, period):
        log_channel_id = await self.config.guild(guild).log_channel_id()
        if not log_channel_id:
            return
        log_channel = guild.get_channel(log_channel_id)
        if not log_channel:
            return
        history = await self.config.guild(guild).ticket_history()
        active_tickets = await self.config.guild(guild).active_tickets()
        stats = await self.config.guild(guild).stats()
        if period == "daily":
            delta = datetime.timedelta(days=1)
            title = "📅 Tägliche Ticket-Zusammenfassung"
        else:
            delta = datetime.timedelta(days=7)
            title = "📊 Wöchentliche Ticket-Zusammenfassung"
        now = datetime.datetime.utcnow()
        start = now - delta
        created_today = 0
        closed_today = 0
        for entry in history:
            created = datetime.datetime.fromisoformat(entry["created_at"])
            if created >= start:
                created_today += 1
            if entry.get("closed_at"):
                closed = datetime.datetime.fromisoformat(entry["closed_at"])
                if closed >= start:
                    closed_today += 1
        open_tickets = len(active_tickets)
        total_closed_all = sum(u.get("closed", 0) for u in stats.values())
        total_reaction = sum(u.get("total_reaction_minutes", 0) for u in stats.values())
        reaction_count = sum(u.get("reaction_count", 0) for u in stats.values())
        avg_reaction = total_reaction / reaction_count if reaction_count else 0
        embed = discord.Embed(title=title, color=discord.Color.gold(), timestamp=now)
        embed.add_field(name="Erstellt", value=created_today, inline=True)
        embed.add_field(name="Geschlossen", value=closed_today, inline=True)
        embed.add_field(name="Offen", value=open_tickets, inline=True)
        embed.add_field(name="Ø Reaktionszeit", value=f"{avg_reaction:.1f} Min", inline=False)
        embed.add_field(name="Gesamt geschlossen", value=total_closed_all, inline=False)
        await log_channel.send(embed=embed)

    # Listener
    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.guild or message.author.bot:
            return
        if message.channel.id not in self._active_channel_cache.get(message.guild.id, set()):
            return
        try:
            tickets = await self.config.guild(message.guild).active_tickets()
            changed = False
            for t in tickets:
                if t["channel_id"] == message.channel.id:
                    if t.get("status") == "PAUSED" and not message.author.guild_permissions.manage_messages:
                        try:
                            await message.delete()
                            await message.channel.send("Dieses Ticket ist pausiert.", delete_after=5)
                        except:
                            pass
                        return
                    t["last_message"] = datetime.datetime.now().isoformat()
                    if not t.get("first_response_at") and message.author.id != t["user_id"]:
                        t["first_response_at"] = datetime.datetime.now().isoformat()
                    changed = True
                    break
            if changed:
                await self.config.guild(message.guild).active_tickets.set(tickets)
        except Exception as e:
            log.error(f"Fehler in on_message: {e}")

    # Auto-Close & Auto-Eskalation Loop
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
                        if t.get("status") in ["WAITING_TEAM", "PAUSED"]:
                            if t.get("status") == "WAITING_TEAM" and auto_esc_hours > 0 and not t.get("escalated"):
                                lm = datetime.datetime.fromisoformat(t.get("last_message", datetime.datetime.now().isoformat()))
                                diff_h = (datetime.datetime.now() - lm).total_seconds() / 3600
                                if diff_h > auto_esc_hours:
                                    await self.auto_escalate_ticket(guild, t, ch)
                                    t["escalated"] = True
                                    t["last_message"] = datetime.datetime.now().isoformat()
                                    changed = True
                            continue
                        if ah == 0:
                            continue
                        lm = datetime.datetime.fromisoformat(t.get("last_message", datetime.datetime.now().isoformat()))
                        diff_h = (datetime.datetime.now() - lm).total_seconds() / 3600
                        if diff_h > (ah - 2) and not t.get("warned", False):
                            try:
                                await ch.send(f"⚠️ <@{t['user_id']}>, dieses Ticket wird in **2 Stunden** automatisch geschlossen.")
                                t["warned"] = True
                                changed = True
                            except:
                                pass
                        if diff_h > ah:
                            await self.close_ticket(ch, "Inaktivität (Auto-Close)", guild.me, is_auto=True)
                            tickets.remove(t)
                            changed = True
                    if changed:
                        await self.config.guild(guild).active_tickets.set(tickets)
            except Exception as e:
                log.error(f"Auto-Close Loop Fehler: {e}")
            await asyncio.sleep(300)

    # Befehle
    @commands.group(name="ticket", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def ticket_cmd(self, ctx):
        await ctx.send_help(ctx.command)

    @ticket_cmd.command(name="help")
    async def ticket_help(self, ctx):
        embed = discord.Embed(title="🎫 Ticket System Hilfe", color=discord.Color.blurple())
        embed.add_field(name="Setup", value="`[p]ticket setup` – Interaktive Einrichtung\n`[p]ticket set` – Geführter Wizard\n`[p]ticket addcat` – Einzelne Kategorie", inline=False)
        embed.add_field(name="Panel", value="`[p]ticket panel #channel` – Panel posten", inline=False)
        embed.add_field(name="Verwaltung", value="`[p]ticket listcat`, `[p]ticket blacklist @User`, `[p]ticket managecats`, `[p]ticket stats`, `[p]ticket history @User`, `[p]ticket syncroles`", inline=False)
        embed.add_field(name="Support", value="`[p]tadd @User`, `[p]tremove @User`, `[p]trename Name`, `[p]ticket forceclose`", inline=False)
        await ctx.send(embed=embed)

    # Interaktiver Setup-Befehl
    @ticket_cmd.command(name="setup")
    async def ticket_setup(self, ctx):
        try:
            view = BaseSetupView(self, ctx)
        except Exception as e:
            return await ctx.send(f"❌ Fehler beim Setup: {e}")
        msg = await ctx.send(embed=discord.Embed(title="🛠️ Basis-Setup", description="Passe die Einstellungen an."), view=view)
        view.message = msg

    # Neuer Wizard-Befehl
    @ticket_cmd.command(name="set", aliases=["wizard"])
    async def ticket_set_wizard(self, ctx):
        """Startet den geführten Einrichtungs-Wizard."""
        guild = ctx.guild
        author = ctx.author
        timeout = 120

        def check(m):
            return m.author == author and m.channel == ctx.channel

        async def ask(question):
            await ctx.send(question)
            try:
                reply = await self.bot.wait_for('message', check=check, timeout=timeout)
                return reply.content
            except asyncio.TimeoutError:
                await ctx.send("⏰ Zeit abgelaufen. Bitte starte den Wizard erneut.")
                return None

        # Log-Channel
        log_channel = None
        while log_channel is None:
            answer = await ask("Bitte gib den **Log-Channel** an (ID oder #Erwähnung):")
            if answer is None: return
            try:
                log_channel = await commands.TextChannelConverter().convert(ctx, answer)
            except:
                await ctx.send("❌ Kanal nicht gefunden. Versuche es erneut.")

        # Admin-Rolle
        admin_role = None
        while admin_role is None:
            answer = await ask("Bitte gib die **Admin-Rolle** an (ID oder Name), die private Threads sehen soll (optional, Enter für keine):")
            if answer is None: return
            if answer.strip() == "":
                admin_role = None
                break
            try:
                admin_role = await commands.RoleConverter().convert(ctx, answer)
            except:
                await ctx.send("❌ Rolle nicht gefunden. Versuche es erneut.")

        # DM-Benachrichtigungen
        dm_on = None
        while dm_on is None:
            answer = await ask("Sollen Nutzer **DM-Benachrichtigungen** erhalten? (ja/nein)")
            if answer is None: return
            if answer.lower() in ['ja', 'j', 'yes', 'y']:
                dm_on = True
            elif answer.lower() in ['nein', 'n', 'no']:
                dm_on = False
            else:
                await ctx.send("❌ Bitte antworte mit 'ja' oder 'nein'.")

        # Auto-Close
        autoclose = None
        while autoclose is None:
            answer = await ask("Nach wie vielen Stunden **Inaktivität** soll ein Ticket automatisch geschlossen werden? (0 = aus)")
            if answer is None: return
            try:
                autoclose = int(answer)
                if autoclose < 0 or autoclose > 500: raise ValueError
            except:
                await ctx.send("❌ Bitte eine Zahl zwischen 0 und 500 eingeben.")
                autoclose = None

        # Cooldown
        cooldown = None
        while cooldown is None:
            answer = await ask("Wie lange soll der **Cooldown** zwischen zwei Tickets eines Nutzers sein? (Minuten, 0 = aus)")
            if answer is None: return
            try:
                cooldown = int(answer)
                if cooldown < 0 or cooldown > 10080: raise ValueError
            except:
                await ctx.send("❌ Bitte eine Zahl zwischen 0 und 10080 eingeben.")
                cooldown = None

        # Max Tickets pro User
        max_tickets = None
        while max_tickets is None:
            answer = await ask("Wie viele **offene Tickets pro Nutzer** sind gleichzeitig erlaubt?")
            if answer is None: return
            try:
                max_tickets = int(answer)
                if max_tickets < 1 or max_tickets > 10: raise ValueError
            except:
                await ctx.send("❌ Bitte eine Zahl zwischen 1 und 10 eingeben.")
                max_tickets = None

        # Threads löschen?
        delete_threads = None
        while delete_threads is None:
            answer = await ask("Sollen **Threads beim Schließen gelöscht** werden? (ja) oder archiviert? (nein)")
            if answer is None: return
            if answer.lower() in ['ja', 'j', 'yes', 'y']:
                delete_threads = True
            elif answer.lower() in ['nein', 'n', 'no']:
                delete_threads = False
            else:
                await ctx.send("❌ Bitte antworte mit 'ja' oder 'nein'.")

        # Auto-Eskalation
        auto_esc = None
        while auto_esc is None:
            answer = await ask("Nach wie vielen Stunden soll ein Ticket mit Status 'Wartet auf Team' **automatisch eskaliert** werden? (0 = aus)")
            if answer is None: return
            try:
                auto_esc = int(answer)
                if auto_esc < 0 or auto_esc > 500: raise ValueError
            except:
                await ctx.send("❌ Bitte eine Zahl zwischen 0 und 500 eingeben.")
                auto_esc = None

        # Kategorien hinzufügen
        categories = {}
        add_more = True
        while add_more:
            answer = await ask("Möchtest du jetzt eine **Support-Kategorie** hinzufügen? (ja/nein)")
            if answer is None: return
            if answer.lower() in ['nein', 'n', 'no']:
                add_more = False
                break
            elif answer.lower() not in ['ja', 'j', 'yes', 'y']:
                await ctx.send("❌ Bitte antworte mit 'ja' oder 'nein'.")
                continue

            cat_name = None
            while cat_name is None:
                cat_name = await ask("**Name der Kategorie** (z.B. Allgemeiner Support):")
                if cat_name is None: return

            cat_desc = await ask("**Beschreibung** (optional, Enter zum Überspringen):")
            if cat_desc is None: return
            if cat_desc.strip() == "":
                cat_desc = None

            cat_emoji = await ask("**Emoji** (optional, Standard: 🎫):")
            if cat_emoji is None: return
            if cat_emoji.strip() == "":
                cat_emoji = "🎫"

            cat_abbr = None
            while cat_abbr is None:
                cat_abbr = await ask("**Abkürzung** für Kanalnamen (z.B. SUP):")
                if cat_abbr is None: return
                if len(cat_abbr) > 10:
                    await ctx.send("❌ Abkürzung max. 10 Zeichen.")
                    cat_abbr = None

            typ = None
            while typ is None:
                answer = await ask("Sollen Tickets als **Textkanal** in einer Kategorie oder als **Thread** erstellt werden? (channel/thread)")
                if answer is None: return
                if answer.lower() in ['channel', 'k', 'kanal']:
                    typ = 'channel'
                elif answer.lower() in ['thread', 't', 'faden']:
                    typ = 'thread'
                else:
                    await ctx.send("❌ Bitte 'channel' oder 'thread' eingeben.")

            discord_cat_id = None
            thread_parent_id = None
            if typ == 'channel':
                while discord_cat_id is None:
                    answer = await ask("Bitte gib die **Discord-Kategorie** an (ID oder Name):")
                    if answer is None: return
                    try:
                        cat = await commands.CategoryChannelConverter().convert(ctx, answer)
                        discord_cat_id = cat.id
                    except:
                        await ctx.send("❌ Kategorie nicht gefunden.")
            else:
                while thread_parent_id is None:
                    answer = await ask("Bitte gib den **Textkanal** an, in dem Threads erstellt werden sollen (ID oder #Erwähnung):")
                    if answer is None: return
                    try:
                        ch = await commands.TextChannelConverter().convert(ctx, answer)
                        thread_parent_id = ch.id
                    except:
                        await ctx.send("❌ Textkanal nicht gefunden.")

            staff_role_id = None
            while staff_role_id is None:
                answer = await ask("Bitte gib die **Support-Rolle** an (ID oder Name):")
                if answer is None: return
                try:
                    role = await commands.RoleConverter().convert(ctx, answer)
                    staff_role_id = role.id
                except:
                    await ctx.send("❌ Rolle nicht gefunden.")

            high_role_id = None
            answer = await ask("**High-Team-Rolle** (optional, Enter zum Überspringen):")
            if answer is None: return
            if answer.strip() != "":
                try:
                    high_role = await commands.RoleConverter().convert(ctx, answer)
                    high_role_id = high_role.id
                except:
                    await ctx.send("❌ Rolle nicht gefunden. High-Team wird übersprungen.")

            cat_max = None
            while cat_max is None:
                answer = await ask("Maximale **aktive Tickets** in dieser Kategorie gleichzeitig? (0 = unbegrenzt)")
                if answer is None: return
                try:
                    cat_max = int(answer)
                    if cat_max < 0 or cat_max > 100: raise ValueError
                except:
                    await ctx.send("❌ Bitte eine Zahl zwischen 0 und 100.")
                    cat_max = None

            cat_id = str(uuid.uuid4())[:8]
            categories[cat_id] = {
                "name": cat_name,
                "description": cat_desc,
                "emoji": cat_emoji,
                "abbr": cat_abbr.upper(),
                "discord_category_id": discord_cat_id,
                "thread_parent_id": thread_parent_id,
                "staff_role_id": staff_role_id,
                "high_team_role_id": high_role_id,
                "max_tickets": cat_max
            }
            await ctx.send(f"✅ Kategorie **{cat_name}** hinzugefügt.")

        # Panel posten?
        panel_channel = None
        answer = await ask("Möchtest du das **Ticket-Panel** jetzt in einem Kanal posten? (ja/nein)")
        if answer is None: return
        if answer.lower() in ['ja', 'j', 'yes', 'y']:
            while panel_channel is None:
                answer = await ask("Bitte gib den **Textkanal** für das Panel an (ID oder #Erwähnung):")
                if answer is None: return
                try:
                    panel_channel = await commands.TextChannelConverter().convert(ctx, answer)
                except:
                    await ctx.send("❌ Kanal nicht gefunden.")

        # Alles speichern
        await self.config.guild(guild).log_channel_id.set(log_channel.id)
        await self.config.guild(guild).admin_role_id.set(admin_role.id if admin_role else None)
        await self.config.guild(guild).dm_notifications.set(dm_on)
        await self.config.guild(guild).autoclose_hours.set(autoclose)
        await self.config.guild(guild).cooldown_minutes.set(cooldown)
        await self.config.guild(guild).max_tickets_per_user.set(max_tickets)
        await self.config.guild(guild).delete_threads_after_close.set(delete_threads)
        await self.config.guild(guild).auto_escalate_hours.set(auto_esc)
        if categories:
            await self.config.guild(guild).categories.set(categories)

        if panel_channel:
            await self.create_panel(panel_channel)

        await ctx.send("✅ **Setup abgeschlossen!**\n"
                       f"Log-Channel: {log_channel.mention}\n"
                       f"Admin-Rolle: {admin_role.mention if admin_role else 'Nicht gesetzt'}\n"
                       f"Kategorien: {len(categories)}\n"
                       f"Panel: {panel_channel.mention if panel_channel else 'Nicht gepostet'}")

    # Weitere Befehle
    @ticket_cmd.command(name="addcat")
    async def ticket_addcat(self, ctx):
        try:
            view = CategorySetupView(self, ctx)
        except Exception as e:
            return await ctx.send(f"❌ Fehler: {e}")
        msg = await ctx.send(embed=discord.Embed(title="🏷️ Kategorie Setup", description="Konfiguriere die Kategorie."), view=view)
        view.message = msg

    @ticket_cmd.command(name="listcat")
    async def ticket_listcat(self, ctx):
        categories = await self.config.guild(ctx.guild).categories()
        if not categories:
            return await ctx.send("❌ Keine Kategorien.")
        text = "**Kategorien:**\n"
        for cid, data in categories.items():
            text += f"- {data.get('name')} (`{cid}`) | Max: {data.get('max_tickets')}\n"
        await ctx.send(text)

    @ticket_cmd.command(name="managecats")
    async def ticket_managecats(self, ctx):
        categories = await self.config.guild(ctx.guild).categories()
        if not categories:
            return await ctx.send("❌ Keine Kategorien.")
        options = [discord.SelectOption(label=c["name"][:100], value=cat_id) for cat_id, c in categories.items()][:25]
        view = discord.ui.View(timeout=300)
        select = discord.ui.Select(placeholder="Kategorie wählen", options=options)
        async def select_cb(inter):
            cat_id = select.values[0]
            cat_data = categories[cat_id]
            ed_view = discord.ui.View(timeout=300)
            btn_edit = discord.ui.Button(label="Bearbeiten", style=discord.ButtonStyle.primary)
            btn_del = discord.ui.Button(label="Löschen", style=discord.ButtonStyle.danger)
            btn_back = discord.ui.Button(label="Abbrechen", style=discord.ButtonStyle.secondary)

            async def edit_cb(inter2):
                await inter2.response.send_modal(CategoryEditModal(self, cat_id, cat_data))

            async def del_cb(inter2):
                cats = await self.config.guild(ctx.guild).categories()
                del cats[cat_id]
                await self.config.guild(ctx.guild).categories.set(cats)
                await self.update_panels(ctx.guild)
                await inter2.response.edit_message(content="Kategorie gelöscht.", view=None)

            async def back_cb(inter2):
                await inter2.response.edit_message(content="Abgebrochen.", view=None)

            btn_edit.callback = edit_cb
            btn_del.callback = del_cb
            btn_back.callback = back_cb
            ed_view.add_item(btn_edit)
            ed_view.add_item(btn_del)
            ed_view.add_item(btn_back)
            await inter.response.edit_message(content=f"Kategorie {cat_data['name']}", view=ed_view)
        select.callback = select_cb
        view.add_item(select)
        await ctx.send("Kategorie wählen:", view=view)

    @ticket_cmd.command(name="panel")
    async def ticket_panel(self, ctx, channel: discord.TextChannel = None):
        if not channel:
            channel = ctx.channel
        categories = await self.config.guild(ctx.guild).categories()
        if not categories:
            return await ctx.send("❌ Erstelle zuerst eine Kategorie.")
        view = TicketPanelView(self)
        options = self._build_panel_options(categories, await self.config.guild(ctx.guild).active_tickets())
        for child in view.children:
            if isinstance(child, discord.ui.Select):
                child.options = options
        embed = await self._build_panel_embed(ctx.guild)
        msg = await channel.send(embed=embed, view=view)
        panels = await self.config.guild(ctx.guild).panels()
        panels.append({"channel_id": channel.id, "msg_id": msg.id})
        await self.config.guild(ctx.guild).panels.set(panels)
        await ctx.send(f"✅ Panel in {channel.mention} gepostet.")

    @ticket_cmd.command(name="blacklist")
    async def ticket_blacklist(self, ctx, user: discord.User, *, reason: str = "Kein Grund"):
        bl = await self.config.guild(ctx.guild).blacklist()
        if user.id not in bl:
            bl.append(user.id)
            await self.config.guild(ctx.guild).blacklist.set(bl)
            await ctx.send(f"✅ {user.mention} gesperrt. Grund: {reason}")
        else:
            await ctx.send("❌ Bereits gesperrt.")

    @ticket_cmd.command(name="unblacklist")
    async def ticket_unblacklist(self, ctx, user: discord.User):
        bl = await self.config.guild(ctx.guild).blacklist()
        if user.id in bl:
            bl.remove(user.id)
            await self.config.guild(ctx.guild).blacklist.set(bl)
            await ctx.send(f"✅ {user.mention} entsperrt.")
        else:
            await ctx.send("❌ Nicht gesperrt.")

    @ticket_cmd.command(name="forceclose")
    async def ticket_forceclose(self, ctx):
        tickets = await self.config.guild(ctx.guild).active_tickets()
        t_data = next((t for t in tickets if t["channel_id"] == ctx.channel.id), None)
        if t_data:
            tickets.remove(t_data)
            await self.config.guild(ctx.guild).active_tickets.set(tickets)
            self._remove_from_active_cache(ctx.guild.id, ctx.channel.id)
            await ctx.send("⚠️ Ticket wird geschlossen...")
            delete_threads = await self.config.guild(ctx.guild).delete_threads_after_close()
            try:
                if isinstance(ctx.channel, discord.Thread):
                    if delete_threads:
                        await ctx.channel.delete()
                    else:
                        new_name = f"archiviert-{ctx.channel.name}"[:100]
                        await ctx.channel.edit(name=new_name, archived=True, locked=True)
                else:
                    await ctx.channel.delete()
            except:
                pass
        else:
            await ctx.send("❌ Kein aktives Ticket.")

    @ticket_cmd.command(name="stats")
    async def ticket_stats(self, ctx, category: str = None):
        stats = await self.config.guild(ctx.guild).stats()
        active_tickets = await self.config.guild(ctx.guild).active_tickets()
        total_created = await self.config.guild(ctx.guild).total_tickets_created()
        category_stats = await self.config.guild(ctx.guild).category_stats()
        categories = await self.config.guild(ctx.guild).categories()
        if not stats and not active_tickets and total_created == 0 and not category_stats:
            return await ctx.send("Keine Statistiken.")
        if category:
            cat_data = None
            cat_id_found = None
            for cid, c in categories.items():
                if c["name"].lower() == category.lower():
                    cat_data = c
                    cat_id_found = cid
                    break
            if not cat_data:
                return await ctx.send("Kategorie nicht gefunden.")
            cs = category_stats.get(cat_id_found, {"created": 0, "closed": 0, "stars": [0,0,0,0,0], "total_duration_minutes": 0, "ticket_count": 0})
            embed = discord.Embed(title=f"Statistik für {cat_data['name']}", color=discord.Color.gold())
            embed.add_field(name="Erstellt", value=cs.get("created", 0))
            embed.add_field(name="Geschlossen", value=cs.get("closed", 0))
            embed.add_field(name="Offen", value=sum(1 for t in active_tickets if t["cat_id"] == cat_id_found))
            return await ctx.send(embed=embed)
        total_closed = sum(u.get("closed", 0) for u in stats.values())
        embed = discord.Embed(title="Support System Statistik", color=discord.Color.gold())
        embed.add_field(name="Erstellt", value=total_created)
        embed.add_field(name="Geschlossen", value=total_closed)
        embed.add_field(name="Offen", value=len(active_tickets))
        if stats:
            sorted_stats = sorted(stats.items(), key=lambda x: x[1].get("closed", 0), reverse=True)
            desc = ""
            for uid, data in sorted_stats[:10]:
                user = ctx.guild.get_member(int(uid))
                name = user.display_name if user else "Unbekannt"
                desc += f"**{name}**: {data.get('closed',0)} geschlossen\n"
            embed.add_field(name="Top Support", value=desc, inline=False)
        await ctx.send(embed=embed)

    @ticket_cmd.command(name="history")
    async def ticket_history(self, ctx, user: discord.User = None):
        if not user:
            user = ctx.author
        history = await self.config.guild(ctx.guild).ticket_history()
        user_tickets = [t for t in history if t["user_id"] == user.id]
        if not user_tickets:
            return await ctx.send(f"Keine Tickets für {user.mention}.")
        embed = discord.Embed(title=f"Ticket-Verlauf für {user.display_name}", color=discord.Color.blue())
        for t in user_tickets[-10:]:
            cat_data = (await self.config.guild(ctx.guild).categories()).get(t["cat_id"], {})
            cat_name = cat_data.get("name", "Unbekannt")
            created = datetime.datetime.fromisoformat(t["created_at"]).strftime("%d.%m.%Y %H:%M")
            closed = datetime.datetime.fromisoformat(t["closed_at"]).strftime("%d.%m.%Y %H:%M")
            stars = t.get("stars", 0)
            reason = t.get("close_reason", "Kein Grund")
            embed.add_field(name=f"{cat_name} – {created}", value=f"Geschlossen: {closed}\nBewertung: {'⭐'*stars if stars else 'Keine'}\nGrund: {reason}", inline=False)
        await ctx.send(embed=embed)

    @ticket_cmd.command(name="syncroles")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def ticket_syncroles(self, ctx):
        guild = ctx.guild
        active_tickets = await self.config.guild(guild).active_tickets()
        history = await self.config.guild(guild).ticket_history()
        thread_ids = set()
        for t in active_tickets:
            thread_ids.add(t["channel_id"])
        for h in history:
            thread_ids.add(h["channel_id"])
        count = 0
        for cid in thread_ids:
            ch = guild.get_channel(cid)
            if isinstance(ch, discord.Thread):
                try:
                    await ch.edit(archived=False, locked=False)
                    await self._sync_roles_to_thread(ch, guild)
                    await ch.edit(archived=True, locked=True)
                    count += 1
                except Exception as e:
                    log.error(f"Sync fehlgeschlagen für {cid}: {e}")
        await ctx.send(f"✅ Rollen wurden zu {count} Threads synchronisiert.")

    @ticket_cmd.command(name="export")
    async def ticket_export(self, ctx):
        stats = await self.config.guild(ctx.guild).stats()
        category_stats = await self.config.guild(ctx.guild).category_stats()
        categories = await self.config.guild(ctx.guild).categories()
        csv_data = "User,Claimed,Closed\n"
        for uid, data in stats.items():
            user = ctx.guild.get_member(int(uid))
            csv_data += f"{user.display_name if user else uid},{data.get('claimed',0)},{data.get('closed',0)}\n"
        csv_data += "\nKategorie,Erstellt,Geschlossen\n"
        for cat_id, cs in category_stats.items():
            cat_name = categories.get(cat_id, {}).get("name", "Unbekannt")
            csv_data += f"{cat_name},{cs.get('created',0)},{cs.get('closed',0)}\n"
        file = discord.File(io.StringIO(csv_data), filename="ticket_stats.csv")
        await ctx.send(file=file)

    @ticket_cmd.command(name="reset")
    async def ticket_reset(self, ctx):
        await self.config.guild(ctx.guild).clear()
        self._active_channel_cache[ctx.guild.id] = set()
        await ctx.send("✅ Konfiguration zurückgesetzt.")

    # Support-Befehle
    @commands.command(name="tadd")
    async def tadd(self, ctx, user: discord.Member):
        tickets = await self.config.guild(ctx.guild).active_tickets()
        t = next((x for x in tickets if x["channel_id"] == ctx.channel.id), None)
        if not t:
            return
        if not await self.is_support(ctx.author, ctx.guild, t):
            return await ctx.send("❌ Keine Berechtigung.", delete_after=5)
        try:
            if isinstance(ctx.channel, discord.Thread):
                await ctx.channel.add_user(user)
            else:
                await ctx.channel.set_permissions(user, view_channel=True, send_messages=True)
            await ctx.send(f"✅ {user.mention} hinzugefügt.")
        except Exception as e:
            await ctx.send(f"❌ Fehler: {e}")

    @commands.command(name="tremove")
    async def tremove(self, ctx, user: discord.Member):
        tickets = await self.config.guild(ctx.guild).active_tickets()
        t = next((x for x in tickets if x["channel_id"] == ctx.channel.id), None)
        if not t:
            return
        if t["user_id"] == user.id:
            return await ctx.send("❌ Du kannst den Ersteller nicht entfernen.")
        if not await self.is_support(ctx.author, ctx.guild, t):
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
    async def trename(self, ctx, *, new_name: str):
        tickets = await self.config.guild(ctx.guild).active_tickets()
        t = next((x for x in tickets if x["channel_id"] == ctx.channel.id), None)
        if not t:
            return
        if not await self.is_support(ctx.author, ctx.guild, t):
            return await ctx.send("❌ Keine Berechtigung.", delete_after=5)
        try:
            await ctx.channel.edit(name=new_name[:100])
            await ctx.send(f"✅ Umbenannt in `{new_name[:100]}`.")
        except Exception as e:
            await ctx.send(f"❌ Fehler: {e}")

    async def is_support(self, member, guild, ticket_data):
        if member.guild_permissions.manage_guild:
            return True
        cat_data = (await self.config.guild(guild).categories()).get(ticket_data.get("cat_id"), {})
        allowed = [cat_data.get("staff_role_id"), cat_data.get("high_team_role_id")]
        return any(rid is not None and rid in [r.id for r in member.roles] for rid in allowed)

    # Core Logic
    def _build_panel_options(self, categories, active_tickets):
        options = []
        for cat_id, c in categories.items():
            active_count = sum(1 for t in active_tickets if t["cat_id"] == cat_id and t.get("status") == "ACTIVE")
            max_t = c.get("max_tickets", 10)
            label = c["name"][:100]
            if max_t > 0:
                label = f"{c['name']} ({active_count}/{max_t})"[:100]
                description = f"{c.get('description', '')} - {int((active_count/max_t)*100)}% ausgelastet"[:100] if c.get('description') else f"{int((active_count/max_t)*100)}% ausgelastet"
            else:
                description = c.get("description", "")[:100] if c.get("description") else None
            options.append(discord.SelectOption(label=label, value=cat_id, description=description, emoji=c.get("emoji")))
        return options[:25]

    async def _build_panel_embed(self, guild):
        categories = await self.config.guild(guild).categories()
        active_tickets = await self.config.guild(guild).active_tickets()

        embed = discord.Embed(
            title="🎫 Support Ticket System",
            description=(
                "Brauchst du Hilfe? Wähle unten im Dropdown-Menü die passende Kategorie aus.\n"
                "Die Auslastung zeigt, wie viele Tickets aktuell in Bearbeitung sind."
            ),
            color=discord.Color.blurple()
        )
        embed.set_footer(text=f"{guild.name} Support Team")

        if not categories:
            embed.add_field(
                name="⚠️ Hinweis",
                value="Es wurden noch keine Kategorien erstellt. Ein Admin muss `[p]ticket addcat` nutzen.",
                inline=False
            )
            return embed

        for cat_id, cat_data in categories.items():
            active_count = sum(
                1 for t in active_tickets
                if t["cat_id"] == cat_id and t.get("status") == "ACTIVE"
            )
            max_tickets = cat_data.get("max_tickets", 10)
            emoji = cat_data.get("emoji", "🎫")
            name = cat_data.get("name", "Unbekannt")

            if max_tickets > 0:
                percent = int((active_count / max_tickets) * 100)
                filled = int((active_count / max_tickets) * 10)
                filled = max(0, min(filled, 10))
                bar = "🟩" * filled + "⬜" * (10 - filled)
                status = f"{active_count}/{max_tickets} {bar} {percent}%"
            else:
                status = f"{active_count} aktiv (unbegrenzt)"

            embed.add_field(
                name=f"{emoji} {name}",
                value=f"`{status}`",
                inline=True
            )

        return embed

    async def update_panels(self, guild):
        categories = await self.config.guild(guild).categories()
        active_tickets = await self.config.guild(guild).active_tickets()
        panels = await self.config.guild(guild).panels()
        valid = []
        for p in panels:
            ch = guild.get_channel(p["channel_id"])
            if not ch:
                continue
            try:
                msg = await ch.fetch_message(p["msg_id"])
                view = TicketPanelView(self)
                options = self._build_panel_options(categories, active_tickets)
                for child in view.children:
                    if isinstance(child, discord.ui.Select):
                        child.options = options
                embed = await self._build_panel_embed(guild)
                await msg.edit(embed=embed, view=view)
                self.bot.add_view(view, message_id=msg.id)
                valid.append(p)
            except:
                pass
        await self.config.guild(guild).panels.set(valid)

    async def finish_base_setup(self, interaction, wizard):
        guild = interaction.guild
        await self.config.guild(guild).log_channel_id.set(wizard.log_channel_id)
        await self.config.guild(guild).admin_role_id.set(wizard.admin_role_id)
        await self.config.guild(guild).dm_notifications.set(wizard.dm_notifications)
        await self.config.guild(guild).autoclose_hours.set(wizard.autoclose_hours)
        await self.config.guild(guild).cooldown_minutes.set(wizard.cooldown_minutes)
        await self.config.guild(guild).max_tickets_per_user.set(wizard.max_tickets_per_user)
        await self.config.guild(guild).delete_threads_after_close.set(wizard.delete_threads_after_close)
        await self.config.guild(guild).auto_escalate_hours.set(wizard.auto_escalate_hours)
        await self.config.guild(guild).show_category_stats.set(wizard.show_category_stats)
        await self.config.guild(guild).use_emoji_charts.set(wizard.use_emoji_charts)
        await interaction.response.edit_message(content="✅ Setup abgeschlossen!", view=None)

    async def save_category(self, interaction, wizard, cat_id=None):
        guild = interaction.guild
        if not cat_id:
            cat_id = str(uuid.uuid4())[:8]
        categories = await self.config.guild(guild).categories()
        categories[cat_id] = {
            "name": wizard.name,
            "description": wizard.description,
            "emoji": wizard.emoji,
            "abbr": wizard.abbr,
            "discord_category_id": wizard.discord_category_id,
            "thread_parent_id": wizard.thread_parent_id,
            "staff_role_id": wizard.staff_role_id,
            "high_team_role_id": wizard.high_team_role_id,
            "max_tickets": wizard.max_tickets
        }
        await self.config.guild(guild).categories.set(categories)
        await self.update_panels(guild)
        await interaction.response.edit_message(content="✅ Kategorie gespeichert!", view=None)

    async def create_ticket(self, interaction, cat_id, issue):
        try:
            await interaction.response.defer(ephemeral=True)
        except:
            pass
        guild = interaction.guild
        user = interaction.user
        config = await self.config.guild(guild).all()
        cat_data = config["categories"].get(cat_id)
        if not cat_data:
            return await interaction.followup.send("❌ Kategorie nicht gefunden.", ephemeral=True)

        staff_role = guild.get_role(cat_data.get("staff_role_id"))
        high_role = guild.get_role(cat_data.get("high_team_role_id"))
        admin_role = guild.get_role(config.get("admin_role_id")) if config.get("admin_role_id") else None

        channel_name = f"{cat_data['abbr']}-{user.name}-{uuid.uuid4().hex[:4]}"[:100]
        ticket_channel = None
        try:
            if cat_data.get("thread_parent_id"):
                parent_ch = guild.get_channel(cat_data["thread_parent_id"])
                if not parent_ch:
                    raise ValueError("Thread-Channel fehlt")
                ticket_channel = await parent_ch.create_thread(name=channel_name, type=discord.ChannelType.private_thread)
                await ticket_channel.add_user(user)

                # Rollenmitglieder parallel hinzufügen
                roles_to_add = []
                if staff_role:
                    roles_to_add.append(staff_role)
                if high_role:
                    roles_to_add.append(high_role)
                if admin_role:
                    roles_to_add.append(admin_role)

                members_to_add = set()
                for role in roles_to_add:
                    members_to_add.update(role.members)

                await asyncio.gather(*[ticket_channel.add_user(m) for m in members_to_add], return_exceptions=True)

            elif cat_data.get("discord_category_id"):
                category = guild.get_channel(cat_data["discord_category_id"])
                if not category:
                    raise ValueError("Kategorie fehlt")
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
                    guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
                }
                if staff_role:
                    overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
                if high_role:
                    overwrites[high_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
                if admin_role:
                    overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
                ticket_channel = await guild.create_text_channel(name=channel_name, category=category, overwrites=overwrites)
            else:
                raise ValueError("Keine Kategorie/Thread konfiguriert")
        except Exception as e:
            await self.send_log(guild, "Fehler bei Ticketerstellung", discord.Color.red(), [("Fehler", str(e))])
            return await interaction.followup.send("❌ Fehler bei der Erstellung.", ephemeral=True)

        now = datetime.datetime.now().isoformat()
        ticket_data = {
            "channel_id": ticket_channel.id,
            "user_id": user.id,
            "cat_id": cat_id,
            "last_message": now,
            "created_at": now,
            "claimed_by": None,
            "escalated": False,
            "status": "ACTIVE",
            "panel_msg_id": None,
            "first_response_at": None
        }
        embed = discord.Embed(title=f"{cat_data['emoji']} Ticket", description=f"**Anliegen:**\n{issue}", color=discord.Color.green())
        mention = f"{user.mention} {staff_role.mention if staff_role else ''}"
        view = TicketControlView(self)
        msg = await ticket_channel.send(content=mention, embed=embed, view=view)
        ticket_data["panel_msg_id"] = msg.id

        tickets = await self.config.guild(guild).active_tickets()
        tickets.append(ticket_data)
        await self.config.guild(guild).active_tickets.set(tickets)

        total = await self.config.guild(guild).total_tickets_created()
        await self.config.guild(guild).total_tickets_created.set(total + 1)

        cat_stats = await self.config.guild(guild).category_stats()
        cs = cat_stats.get(cat_id, {"created": 0, "closed": 0, "stars": [0,0,0,0,0], "total_duration_minutes": 0, "ticket_count": 0})
        cs["created"] += 1
        cat_stats[cat_id] = cs
        await self.config.guild(guild).category_stats.set(cat_stats)

        self._add_to_active_cache(guild.id, ticket_channel.id)

        if config.get("dm_notifications"):
            await self.send_dm(user, "Ticket erstellt", f"Dein Ticket: {ticket_channel.mention}")

        await self.send_log(guild, "Ticket eröffnet", discord.Color.green(), [("User", user.mention), ("Kategorie", cat_data.get("name")), ("Kanal", ticket_channel.mention)])
        await self.update_panels(guild)
        await interaction.followup.send(f"✅ Ticket erstellt: {ticket_channel.mention}", ephemeral=True)

    async def claim_ticket(self, interaction, view):
        guild = interaction.guild
        tickets = await self.config.guild(guild).active_tickets()
        t = next((x for x in tickets if x["channel_id"] == interaction.channel.id), None)
        if not t:
            return await interaction.response.send_message("Kein Ticket.", ephemeral=True)
        if interaction.user.id == t["user_id"]:
            return await interaction.response.send_message("Du bist der Ersteller.", ephemeral=True)
        if not await self.is_support(interaction.user, guild, t):
            return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)

        stats = await self.config.guild(guild).stats()
        us = stats.get(str(interaction.user.id), {"claimed": 0, "closed": 0, "stars": [0,0,0,0,0]})
        us["claimed"] += 1
        stats[str(interaction.user.id)] = us
        await self.config.guild(guild).stats.set(stats)

        for x in tickets:
            if x["channel_id"] == t["channel_id"]:
                x["claimed_by"] = interaction.user.id
                break
        await self.config.guild(guild).active_tickets.set(tickets)

        for child in view.children:
            if child.custom_id == "support_ticket_claim_btn":
                child.label = "Freigeben"
                child.style = discord.ButtonStyle.secondary
        await interaction.response.edit_message(view=view)
        await interaction.channel.send(f"✅ {interaction.user.mention} hat übernommen.")

    async def escalate_ticket(self, interaction, view):
        guild = interaction.guild
        tickets = await self.config.guild(guild).active_tickets()
        t = next((x for x in tickets if x["channel_id"] == interaction.channel.id), None)
        if not t:
            return await interaction.response.send_message("Kein Ticket.", ephemeral=True)
        if interaction.user.id == t["user_id"]:
            return await interaction.response.send_message("Du bist der Ersteller.", ephemeral=True)
        if not await self.is_support(interaction.user, guild, t):
            return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
        cat_data = (await self.config.guild(guild).categories()).get(t["cat_id"], {})
        high_role_id = cat_data.get("high_team_role_id")
        if not high_role_id:
            return await interaction.response.send_message("Kein High-Team konfiguriert.", ephemeral=True)
        high_role = guild.get_role(high_role_id)
        for x in tickets:
            if x["channel_id"] == t["channel_id"]:
                x["escalated"] = True
                x["claimed_by"] = None
                break
        await self.config.guild(guild).active_tickets.set(tickets)
        await interaction.response.send_message(f"⚠️ Eskaliert an {high_role.mention}.")

    async def change_status(self, interaction, status, view):
        guild = interaction.guild
        tickets = await self.config.guild(guild).active_tickets()
        t = next((x for x in tickets if x["channel_id"] == interaction.channel.id), None)
        if not t:
            return await interaction.response.send_message("Kein Ticket.", ephemeral=True)
        if interaction.user.id == t["user_id"]:
            return await interaction.response.send_message("Du bist der Ersteller.", ephemeral=True)
        if not await self.is_support(interaction.user, guild, t):
            return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
        for x in tickets:
            if x["channel_id"] == t["channel_id"]:
                x["status"] = status
                break
        await self.config.guild(guild).active_tickets.set(tickets)
        await interaction.response.send_message(f"Status geändert zu {status}.")

    async def close_ticket(self, channel, reason, user, interaction=None, is_auto=False):
        guild = channel.guild
        config = await self.config.guild(guild).all()
        ticket_data = next((t for t in config["active_tickets"] if t["channel_id"] == channel.id), None)
        if not ticket_data:
            return
        if user.id == ticket_data["user_id"]:
            if interaction:
                return await interaction.response.send_message("Du kannst dein eigenes Ticket nicht schließen.", ephemeral=True)
            return

        try:
            if not is_auto:
                stats = config.get("stats", {})
                closed_by = ticket_data.get("claimed_by") or user.id
                us = stats.get(str(closed_by), {"claimed": 0, "closed": 0, "stars": [0,0,0,0,0]})
                us["closed"] += 1
                created_at = datetime.datetime.fromisoformat(ticket_data["created_at"])
                duration = (datetime.datetime.now() - created_at).total_seconds() / 60
                us["total_duration_minutes"] = us.get("total_duration_minutes", 0) + duration
                us["ticket_count"] = us.get("ticket_count", 0) + 1
                stats[str(closed_by)] = us
                await self.config.guild(guild).stats.set(stats)

                cat_stats = config.get("category_stats", {})
                cs = cat_stats.get(ticket_data["cat_id"], {"created": 0, "closed": 0, "stars": [0,0,0,0,0], "total_duration_minutes": 0, "ticket_count": 0})
                cs["closed"] += 1
                cs["total_duration_minutes"] += duration
                cs["ticket_count"] += 1
                cat_stats[ticket_data["cat_id"]] = cs
                await self.config.guild(guild).category_stats.set(cat_stats)
        except Exception as e:
            log.error(f"Fehler beim Speichern der Statistiken: {e}")

        try:
            history_entry = {
                "user_id": ticket_data["user_id"],
                "cat_id": ticket_data["cat_id"],
                "channel_id": channel.id,
                "created_at": ticket_data["created_at"],
                "closed_at": datetime.datetime.now().isoformat(),
                "close_reason": reason,
                "stars": 0
            }
            history = await self.config.guild(guild).ticket_history()
            history.append(history_entry)
            if len(history) > 100:
                history = history[-100:]
            await self.config.guild(guild).ticket_history.set(history)
        except Exception as e:
            log.error(f"Fehler beim Speichern der Historie: {e}")

        try:
            messages_html = ""
            async for message in channel.history(limit=None, oldest_first=True):
                content = discord.utils.escape_html(message.content) if message.content else ""
                if message.attachments:
                    content += f"<br><i>Anhänge: {', '.join([a.url for a in message.attachments])}</i>"
                messages_html += MESSAGE_HTML.format(
                    avatar_url=message.author.display_avatar.url,
                    author=message.author.display_name,
                    color=str(message.author.color) if message.author.color.value else "#ffffff",
                    timestamp=message.created_at.strftime("%d.%m.%Y %H:%M"),
                    content=content
                )
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
                await log_channel.send(embed=discord.Embed(title="Ticket geschlossen", color=discord.Color.red()), file=transcript_file)

            user_obj = guild.get_member(ticket_data["user_id"]) or self.bot.get_user(ticket_data["user_id"])
            if config.get("dm_notifications") and user_obj:
                await user_obj.send(embed=discord.Embed(title="Ticket geschlossen", description=f"Grund: {reason}"), file=discord.File(io.StringIO(html_content), filename=f"transcript-{channel.id}.html"))
        except Exception as e:
            log.error(f"Fehler beim Transkript: {e}")

        try:
            tickets = await self.config.guild(guild).active_tickets()
            tickets = [t for t in tickets if t["channel_id"] != channel.id]
            await self.config.guild(guild).active_tickets.set(tickets)
            self._remove_from_active_cache(guild.id, channel.id)
        except Exception as e:
            log.error(f"Fehler beim Entfernen: {e}")

        if not is_auto:
            try:
                msg = await channel.send(embed=discord.Embed(title="⭐ Bewertung", description="Bitte bewerte den Support."), view=ReviewView(self, ticket_data))
                msg.view.message = msg
            except:
                pass
        else:
            await self.delete_ticket_channel(channel, ticket_data, 0)

    async def delete_ticket_channel(self, channel, ticket_data, stars):
        guild = channel.guild
        config = await self.config.guild(guild).all()
        if stars > 0 and ticket_data.get("claimed_by"):
            stats = config.get("stats", {})
            claimer = str(ticket_data["claimed_by"])
            us = stats.get(claimer, {"claimed": 0, "closed": 0, "stars": [0,0,0,0,0]})
            us["stars"][stars-1] += 1
            stats[claimer] = us
            await self.config.guild(guild).stats.set(stats)
        tickets = await self.config.guild(guild).active_tickets()
        tickets = [t for t in tickets if t["channel_id"] != channel.id]
        await self.config.guild(guild).active_tickets.set(tickets)
        self._remove_from_active_cache(guild.id, channel.id)
        try:
            if isinstance(channel, discord.Thread):
                if config.get("delete_threads_after_close", False):
                    await channel.delete()
                else:
                    await channel.edit(archived=False, locked=False)
                    await self._sync_roles_to_thread(channel, guild)
                    new_name = f"archiviert-{channel.name}"[:100]
                    await channel.edit(name=new_name, archived=True, locked=True)
            else:
                await channel.delete()
        except Exception as e:
            log.error(f"Fehler beim Schließen des Kanals: {e}")
        await self.update_panels(guild)

    async def auto_escalate_ticket(self, guild, ticket_data, channel):
        config = await self.config.guild(guild).all()
        cat_data = config["categories"].get(ticket_data["cat_id"], {})
        high_role_id = cat_data.get("high_team_role_id")
        if not high_role_id:
            return
        high_role = guild.get_role(high_role_id)
        await channel.send(f"⚠️ Automatische Eskalation! {high_role.mention} wurde benachrichtigt.")

async def setup(bot):
    await bot.add_cog(SupportCog(bot))
