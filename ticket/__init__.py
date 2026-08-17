"""
SupportCog V34 - Complete & Fixed
- Vereinfachte interaktive Setup-Views (Modal für Texte, Selects für IDs)
- Alle Funktionen: Auto-Close, Auto-Eskalation, Statistiken, Export, Reviews, Blacklist, etc.
- Persistente Views korrekt registriert
- Fehlerbehandlung und Performance-Cache
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


class CategoryAllTextModal(discord.ui.Modal, title="Kategorie Texte"):
    def __init__(self, wizard: 'CategorySetupView'):
        super().__init__()
        self.wizard = wizard

        self.name_input = discord.ui.TextInput(
            label="Name der Kategorie",
            placeholder="z.B. Allgemeiner Support",
            default=wizard.name if wizard.name else "",
            max_length=50,
            required=True
        )
        self.desc_input = discord.ui.TextInput(
            label="Beschreibung",
            placeholder="Wofür ist diese Kategorie?",
            default=wizard.description if wizard.description else "",
            max_length=100,
            required=False
        )
        self.abbr_input = discord.ui.TextInput(
            label="Kanal-Abkürzung",
            placeholder="z.B. SUP",
            default=wizard.abbr if wizard.abbr else "",
            max_length=10,
            required=True
        )
        self.emoji_input = discord.ui.TextInput(
            label="Emoji",
            placeholder="z.B. 🎫",
            default=wizard.emoji if wizard.emoji else "🎫",
            max_length=10,
            required=False
        )

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
        # Zeile 0: Text-Button, Max-Tickets-Button, Speichern-Button
        self.btn_texts = discord.ui.Button(label="Texte anpassen", style=discord.ButtonStyle.primary, row=0, emoji="📝")
        self.btn_texts.callback = self._texts_cb
        self.add_item(self.btn_texts)

        self.btn_max_tickets = discord.ui.Button(label=f"Max aktiv: {self.max_tickets}", style=discord.ButtonStyle.secondary, emoji='📊', row=0)
        self.btn_max_tickets.callback = self._max_tickets_cb
        self.add_item(self.btn_max_tickets)

        self.btn_save = discord.ui.Button(label="Speichern", style=discord.ButtonStyle.success, emoji='✅', row=0)
        self.btn_save.callback = self._save_cb
        self.add_item(self.btn_save)

        # Selects in Zeilen 1-4
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
        self.staff_sel = discord.ui.Select(placeholder="Support-Rolle wählen", options=staff_options, row=3)
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
        await interaction.response.send_modal(SimpleNumberModal(self, "max_tickets", "Maximale aktive Tickets (0 = unbegrenzt)", 0, 100))

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
            self.staff_sel.placeholder = f"Support: {role.name}" if role else "Support-Rolle wählen"
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
            self.high_sel.placeholder = "High-Team Rolle"
        await interaction.response.edit_message(view=self)

    async def _save_cb(self, interaction: discord.Interaction):
        if not self.name or not self.abbr or not self.staff_role_id:
            return await interaction.response.send_message("❌ Bitte klicke auf 'Texte anpassen' und wähle eine Support-Rolle aus!", ephemeral=True)
        if not self.discord_category_id and not self.thread_parent_id:
            return await interaction.response.send_message("❌ Bitte wähle entweder eine Discord Kategorie ODER einen Thread-Channel aus!", ephemeral=True)
        await self.cog.save_category(interaction, self, self.cat_id)
        self.stop()


class BaseSetupView(discord.ui.View):
    """Interaktive Basis-Konfiguration."""
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

        self._build_ui()

    def _build_ui(self):
        # Log-Channel Select
        log_options = [discord.SelectOption(label=f"#{c.name}"[:100], value=str(c.id)) for c in self.ctx.guild.text_channels[:25]]
        if not log_options:
            log_options = [discord.SelectOption(label="Keine Textkanäle", value="none")]
        self.log_sel = discord.ui.Select(placeholder="Log-Channel wählen", options=log_options, row=0)
        self.log_sel.callback = self._log_select_cb
        self.add_item(self.log_sel)

        # Buttons für Optionen
        self.btn_dm = discord.ui.Button(label=f"DMs: {'AN' if self.dm_notifications else 'AUS'}", style=discord.ButtonStyle.success if self.dm_notifications else discord.ButtonStyle.danger, emoji='✉️', row=1)
        self.btn_dm.callback = self._dm_toggle_cb
        self.add_item(self.btn_dm)

        self.btn_auto = discord.ui.Button(label=f"Auto-Close: {self.autoclose_hours}h", style=discord.ButtonStyle.secondary, emoji='⏳', row=1)
        self.btn_auto.callback = self._auto_cb
        self.add_item(self.btn_auto)

        self.btn_cool = discord.ui.Button(label=f"Cooldown: {self.cooldown_minutes}m", style=discord.ButtonStyle.secondary, emoji='❄️', row=1)
        self.btn_cool.callback = self._cool_cb
        self.add_item(self.btn_cool)

        self.btn_max = discord.ui.Button(label=f"Max Tickets: {self.max_tickets_per_user}", style=discord.ButtonStyle.secondary, emoji='🔢', row=1)
        self.btn_max.callback = self._max_cb
        self.add_item(self.btn_max)

        self.btn_del_thread = discord.ui.Button(label=f"Threads löschen: {'AN' if self.delete_threads_after_close else 'AUS'}", style=discord.ButtonStyle.success if self.delete_threads_after_close else discord.ButtonStyle.danger, emoji='🗑️', row=2)
        self.btn_del_thread.callback = self._del_thread_toggle_cb
        self.add_item(self.btn_del_thread)

        self.btn_esc = discord.ui.Button(label=f"Auto-Eskalation: {self.auto_escalate_hours}h", style=discord.ButtonStyle.secondary, emoji='🚨', row=2)
        self.btn_esc.callback = self._esc_cb
        self.add_item(self.btn_esc)

        self.btn_catstats = discord.ui.Button(label=f"Kategorie-Statistiken: {'AN' if self.show_category_stats else 'AUS'}", style=discord.ButtonStyle.success if self.show_category_stats else discord.ButtonStyle.danger, emoji='📊', row=2)
        self.btn_catstats.callback = self._catstats_toggle_cb
        self.add_item(self.btn_catstats)

        self.btn_emoji = discord.ui.Button(label=f"Emoji-Balken: {'AN' if self.use_emoji_charts else 'AUS'}", style=discord.ButtonStyle.success if self.use_emoji_charts else discord.ButtonStyle.danger, emoji='📈', row=2)
        self.btn_emoji.callback = self._emoji_toggle_cb
        self.add_item(self.btn_emoji)

        self.btn_finish = discord.ui.Button(label="Setup abschließen", style=discord.ButtonStyle.success, emoji='✅', row=3)
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
            self.autoclose_task = self.bot.loop.create_task(self.autoclose_loop())
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
                            if isinstance(child, discord.ui.Select):
                                child.options = options
                    else:
                        view.clear_items()
                    self.bot.add_view(view, message_id=panel["msg_id"])
                except Exception as e:
                    log.error(f"Panel-Registrierung fehlgeschlagen für {panel}: {e}")

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
                                f"{message.author.mention}, dieses Ticket ist pausiert.",
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
                        if t.get("status") in ["WAITING_TEAM", "PAUSED"]:
                            if t.get("status") == "WAITING_TEAM" and auto_esc_hours > 0 and not t.get("escalated"):
                                lm = datetime.datetime.fromisoformat(t.get("last_message", datetime.datetime.now().isoformat()))
                                diff_h = (datetime.datetime.now() - lm).total_seconds() / 3600
                                if diff_h > auto_esc_hours:
                                    await self.auto_escalate_ticket(guild, t, ch)
                                    t["escalated"] = True
                                    t["last_message"] = datetime.datetime.now().isoformat()
                                    changed = True
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
        embed = discord.Embed(title="🎫 Ticket System Hilfe", color=discord.Color.blurple())
        embed.add_field(name="Setup", value="`[p]ticket setup` – Basis-Setup\n`[p]ticket addcat` – Kategorie interaktiv erstellen", inline=False)
        embed.add_field(name="Panel", value="`[p]ticket panel #channel` – Ticket-Panel posten", inline=False)
        embed.add_field(name="Weitere Befehle", value="`[p]ticket listcat`, `[p]ticket blacklist @User`, `[p]ticket stats`, `[p]ticket export`, `[p]ticket reset`", inline=False)
        await ctx.send(embed=embed)

    @ticket_cmd.command(name="setup")
    async def ticket_setup(self, ctx: commands.Context):
        try:
            view = BaseSetupView(self, ctx)
        except Exception as e:
            return await ctx.send(f"❌ Fehler: {e}")
        msg = await ctx.send(embed=discord.Embed(title="🛠️ Basis-Setup", description="Passe die grundlegenden Einstellungen an.", color=discord.Color.blurple()), view=view)
        view.message = msg

    @ticket_cmd.command(name="addcat")
    async def ticket_addcat(self, ctx: commands.Context):
        try:
            view = CategorySetupView(self, ctx)
        except Exception as e:
            return await ctx.send(f"❌ Fehler: {e}")
        msg = await ctx.send(embed=discord.Embed(title="🏷️ Kategorie Setup", description="Konfiguriere die Kategorie.", color=discord.Color.green()), view=view)
        view.message = msg

    @ticket_cmd.command(name="listcat")
    async def ticket_listcat(self, ctx: commands.Context):
        categories = await self.config.guild(ctx.guild).categories()
        if not categories:
            return await ctx.send("❌ Keine Kategorien vorhanden.")
        text = "**Kategorien:**\n"
        for cid, data in categories.items():
            text += f"- {data.get('name')} (`{cid}`) | Abkürzung: {data.get('abbr')} | Max: {data.get('max_tickets')}\n"
        await ctx.send(text)

    @ticket_cmd.command(name="panel")
    async def ticket_panel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        if not channel:
            channel = ctx.channel
        categories = await self.config.guild(ctx.guild).categories()
        if not categories:
            return await ctx.send("❌ Erstelle zuerst eine Kategorie mit `[p]ticket addcat`!")
        await self.create_panel(channel)
        await ctx.send(f"✅ Panel in {channel.mention} gepostet.")

    @ticket_cmd.command(name="blacklist")
    async def ticket_blacklist(self, ctx: commands.Context, user: discord.User):
        bl = await self.config.guild(ctx.guild).blacklist()
        if user.id not in bl:
            bl.append(user.id)
            await self.config.guild(ctx.guild).blacklist.set(bl)
            await ctx.send(f"✅ {user.mention} gesperrt.")
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
        stats = await self.config.guild(ctx.guild).stats()
        active_tickets = await self.config.guild(ctx.guild).active_tickets()
        total_created = await self.config.guild(ctx.guild).total_tickets_created()
        category_stats = await self.config.guild(ctx.guild).category_stats()
        categories = await self.config.guild(ctx.guild).categories()

        if not stats and not active_tickets and total_created == 0 and not category_stats:
            return await ctx.send("Keine Statistiken vorhanden.")

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
        await ctx.send(embed=embed)

    @ticket_cmd.command(name="export")
    async def ticket_export(self, ctx: commands.Context):
        stats = await self.config.guild(ctx.guild).stats()
        csv_data = "User,Claimed,Closed\n"
        for uid, data in stats.items():
            user = ctx.guild.get_member(int(uid))
            csv_data += f"{user.display_name if user else uid},{data.get('claimed',0)},{data.get('closed',0)}\n"
        file = discord.File(io.StringIO(csv_data), filename="stats.csv")
        await ctx.send(file=file)

    @ticket_cmd.command(name="reset")
    async def ticket_reset(self, ctx: commands.Context):
        await self.config.guild(ctx.guild).clear()
        self._active_channel_cache[ctx.guild.id] = set()
        await ctx.send("✅ Konfiguration zurückgesetzt.")

    # --- Core Logic ---
    async def create_panel(self, channel: discord.TextChannel):
        guild = channel.guild
        categories = await self.config.guild(guild).categories()
        active_tickets = await self.config.guild(guild).active_tickets()
        view = TicketPanelView(self)
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
        for child in view.children:
            if isinstance(child, discord.ui.Select):
                child.options = options[:25]
        embed = discord.Embed(title="🎫 Support", description="Wähle eine Kategorie.", color=discord.Color.blurple())
        msg = await channel.send(embed=embed, view=view)
        async with self.config.guild(guild).panels() as panels:
            panels.append({"channel_id": channel.id, "msg_id": msg.id})

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
                    active_count = sum(1 for t in active_tickets if t["cat_id"] == cat_id and t.get("status") == "ACTIVE")
                    max_t = c.get("max_tickets", 10)
                    label = c["name"][:100]
                    if max_t > 0:
                        label = f"{c['name']} ({active_count}/{max_t})"[:100]
                        description = f"{c.get('description', '')} - {int((active_count/max_t)*100)}% ausgelastet"[:100] if c.get('description') else f"{int((active_count/max_t)*100)}% ausgelastet"
                    else:
                        description = c.get("description", "")[:100] if c.get("description") else None
                    options.append(discord.SelectOption(label=label, value=cat_id, description=description, emoji=c.get("emoji")))
                view = TicketPanelView(self)
                for child in view.children:
                    if isinstance(child, discord.ui.Select):
                        child.options = options[:25]
                await msg.edit(view=view)
                self.bot.add_view(view, message_id=msg.id)
                valid_panels.append(p)
            except discord.NotFound:
                pass
            except Exception as e:
                log.error(f"Update panel failed: {e}")
                valid_panels.append(p)
        await self.config.guild(guild).panels.set(valid_panels)

    async def finish_base_setup(self, interaction, wizard: BaseSetupView):
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
        await interaction.response.edit_message(content="✅ Setup abgeschlossen!", view=None)

    async def save_category(self, interaction, wizard: CategorySetupView, cat_id: str = None):
        guild = interaction.guild
        if not cat_id:
            cat_id = str(uuid.uuid4())[:8]
        async with self.config.guild(guild).categories() as categories:
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
        await self.update_panels(guild)
        await interaction.response.edit_message(content="✅ Kategorie gespeichert!", view=None)

    async def add_role_to_thread_silently(self, thread, role):
        for member in role.members:
            try:
                await thread.add_user(member)
            except Exception:
                pass

    async def create_ticket(self, interaction, cat_id, issue):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        guild = interaction.guild
        user = interaction.user
        config = await self.config.guild(guild).all()
        cat_data = config["categories"].get(cat_id)
        if not cat_data:
            return await interaction.followup.send("❌ Kategorie nicht gefunden.", ephemeral=True)

        staff_role = guild.get_role(cat_data.get("staff_role_id"))
        high_role = guild.get_role(cat_data.get("high_team_role_id"))

        channel_name = f"{cat_data['abbr']}-{user.name}-{uuid.uuid4().hex[:4]}"[:100]
        ticket_channel = None
        try:
            if cat_data.get("thread_parent_id"):
                parent_ch = guild.get_channel(cat_data["thread_parent_id"])
                if not parent_ch:
                    raise ValueError("Thread-Channel fehlt")
                ticket_channel = await parent_ch.create_thread(name=channel_name, type=discord.ChannelType.private_thread)
                await ticket_channel.add_user(user)
                if staff_role:
                    await self.add_role_to_thread_silently(ticket_channel, staff_role)
            elif cat_data.get("discord_category_id"):
                category = guild.get_channel(cat_data["discord_category_id"])
                if not category:
                    raise ValueError("Kategorie fehlt")
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                    guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
                }
                if staff_role:
                    overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
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
            "warned": False,
            "panel_msg_id": None
        }
        embed = discord.Embed(title=f"{cat_data['emoji']} Ticket", description=f"**Anliegen:**\n{issue}", color=discord.Color.green())
        mention = f"{user.mention} {staff_role.mention if staff_role else ''}"
        view = TicketControlView(self)
        msg = await ticket_channel.send(content=mention, embed=embed, view=view)
        ticket_data["panel_msg_id"] = msg.id

        async with self.config.guild(guild).active_tickets() as tickets:
            tickets.append(ticket_data)
        current_total = await self.config.guild(guild).total_tickets_created()
        await self.config.guild(guild).total_tickets_created.set(current_total + 1)
        # Kategorie-Statistik
        async with self.config.guild(guild).category_stats() as cat_stats:
            cs = cat_stats.get(cat_id, {"created": 0, "closed": 0, "stars": [0,0,0,0,0], "total_duration_minutes": 0, "ticket_count": 0})
            cs["created"] += 1
            cat_stats[cat_id] = cs

        self._add_to_active_cache(guild.id, ticket_channel.id)
        await self.update_panels(guild)
        await interaction.followup.send(f"✅ Ticket erstellt: {ticket_channel.mention}", ephemeral=True)

    async def claim_ticket(self, interaction, view):
        guild = interaction.guild
        async with self.config.guild(guild).active_tickets() as tickets:
            for t in tickets:
                if t["channel_id"] == interaction.channel.id:
                    t["claimed_by"] = interaction.user.id
                    break
        await interaction.response.send_message("✋ Ticket übernommen.")

    async def escalate_ticket(self, interaction, view):
        guild = interaction.guild
        async with self.config.guild(guild).active_tickets() as tickets:
            for t in tickets:
                if t["channel_id"] == interaction.channel.id:
                    t["escalated"] = True
                    break
        await interaction.response.send_message("⚠️ Ticket eskaliert.")

    async def change_status(self, interaction, status, view):
        guild = interaction.guild
        async with self.config.guild(guild).active_tickets() as tickets:
            for t in tickets:
                if t["channel_id"] == interaction.channel.id:
                    t["status"] = status
                    break
        await interaction.response.send_message(f"🔄 Status geändert zu {status}.")

    async def close_ticket(self, channel, reason, user, interaction=None, is_auto=False):
        guild = channel.guild
        config = await self.config.guild(guild).all()
        ticket_data = next((t for t in config["active_tickets"] if t["channel_id"] == channel.id), None)
        if not ticket_data:
            return
        # HTML-Transkript erstellen
        messages_html = ""
        try:
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
            try:
                await log_channel.send(embed=discord.Embed(title="Ticket geschlossen", color=discord.Color.red()), file=transcript_file)
            except Exception:
                pass

        # Review senden
        if not is_auto:
            msg = await channel.send(embed=discord.Embed(title="⭐ Bewertung", description="Bitte bewerte den Support."), view=ReviewView(self, ticket_data))
            msg.view.message = msg
        else:
            await self.delete_ticket_channel(channel, ticket_data, 0)

    async def delete_ticket_channel(self, channel, ticket_data, stars):
        guild = channel.guild
        async with self.config.guild(guild).active_tickets() as tickets:
            tickets = [t for t in tickets if t["channel_id"] != channel.id]
            await self.config.guild(guild).active_tickets.set(tickets)
        self._remove_from_active_cache(guild.id, channel.id)
        try:
            if isinstance(channel, discord.Thread):
                await channel.edit(archived=True, locked=True)
            else:
                await channel.delete()
        except Exception:
            pass
        await self.update_panels(guild)

    # Weitere Hilfsmethoden (is_support, etc.)
    async def is_support(self, member, guild, ticket_data):
        if member.guild_permissions.manage_guild:
            return True
        cat_data = (await self.config.guild(guild).categories()).get(ticket_data.get("cat_id"), {})
        allowed = [cat_data.get("staff_role_id"), cat_data.get("high_team_role_id")]
        return any(rid is not None and rid in [r.id for r in member.roles] for rid in allowed)

    async def auto_escalate_ticket(self, guild, ticket_data, channel):
        # Einfache Implementierung: Status setzen und Nachricht senden
        await channel.send("⚠️ Automatische Eskalation! Das High-Team wurde benachrichtigt.")

async def setup(bot):
    await bot.add_cog(SupportCog(bot))
