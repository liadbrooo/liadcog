"""
SupportCog V1 – Neugeschrieben, bereinigt und erweitert
=======================================================

Ein vollständiges Ticket-/Support-System für Red-DiscordBot (V3).

Highlights
----------
- Interaktives Basis-Setup (UI) und geführter Text-Wizard
- Beliebig viele Kategorien: Tickets als Textkanäle oder private Threads
- Ticket-Panel mit Dropdown, Live-Auslastung und optionalen Emoji-Balken
- Ticket-Steuerung in jedem Ticket: Übernehmen/Freigeben, Eskalieren,
  Schließen (mit Grund), Status-Wechsel inkl. Live-Steuerungs-Embed
- Auto-Close mit Vorwarnung, Auto-Eskalation, Cooldown, Ticket-Limits,
  Kategorie-Limits und Blacklist
- HTML-Transkripte (Log-Channel + DM), Ticket-Verlauf, umfangreiche
  Statistiken (Reaktionszeit, Dauer, Sterne), CSV-Export
- Tägliche und wöchentliche Zusammenfassungen (dediziert persistent)
- Rollen-Sync für (archivierte) Threads, Aufräum-Listener für gelöschte
  Kanäle/Threads, fortlaufende Ticket-Nummern

Kompatibilität
--------------
Die Config-Kennung wurde übernommen: bestehende Einstellungen,
Kategorien und Statistiken aus früheren Versionen bleiben erhalten.
Neue Konfigurations-Schlüssel werden automatisch mit Defaults ergänzt.

Bekannte, hier behobene Kernfehler der Vorgängerversionen
---------------------------------------------------------
- `[p]ticket set` (Wizard) rief `self.create_panel()` auf, obwohl diese
  Methode nie existierte -> Absturz beim Posten des Panels
- Der 'Freigeben'-Button nach dem Übernehmen führte dieselbe Claim-Logik
  aus statt das Ticket freizugeben
- `msg.view.message = msg` nach dem Bewertungs-Embed: `discord.Message`
  besitzt kein `.view`-Attribut -> Timeout-Aufräumen funktionierte nie
- Der Wizard überschrieb ALLE bestehenden Kategorien
- `show_category_stats` und `use_emoji_charts` hatten keinerlei Wirkung
- Reaktionszeit (`total_reaction_minutes`) wurde gelesen, aber nie
  geschrieben; Sterne landeten nie im Verlauf
- `_sync_roles_to_thread` nutzte immer nur die ERSTE Kategorie
- `close_ticket` antwortete nach `defer()` erneut per `response` ->
  'InteractionResponded'-Fehler
- forceclose hinterließ keinerlei Verlauf/Statistik/Transkript
- threads konnten wegen `auto_archive_duration`-Defaults mitten in der
  Bearbeitung archivieren; gemischte naive/aware-Zeitstempel

Änderungen in V1.1
------------------
- **Thread-Bug (Hauptursache für "Threads werden nicht archiviert"):**
  `Guild.get_channel` findet Threads NICHT. Der Auto-Close-Loop hielt
  dadurch jedes Thread-Ticket binnen Minuten für "gelöscht" und entfernte
  es aus dem Tracking. Jetzt wird überall `get_channel_or_thread` plus
  `fetch_channel`-Fallback (für archivierte Threads nach Neustart)
  verwendet.
- Sichtbarkeit: Support-/High-Team-Rollen sehen Kategorien-Tickets immer –
  neue Rollenmitglieder werden bei Rollenwechsel & Serverbeitritt
  automatisch zu offenen Ticket-Threads hinzugefügt. Die Admin-Rolle
  (z. B. Inhaber) gilt überall als Support und sieht/steuert alle Tickets.
- Thread-Archivierung robuster: ent-archivieren → Rollen synchronisieren →
  umbenennen → archivieren+sperren, jeder Schritt einzeln abgesichert.
- Offene Ticket-Threads werden zyklisch geprüft: versehentlich archivierte
  werden ent-archiviert und auf 7 Tage Auto-Archive gesetzt (heilt auch
  Alttickets). Beim Anlegen wird 'Threads verwalten' im Parent abgesichert.
- Zusammenfassungen senden beim allerersten Start keine Leermeldung mehr.
- PAUSED-Tickets: High-Team darf jetzt ebenfalls schreiben.
- Schließen-Button: nur noch Ersteller und Support-Team (plus Admin-Rolle).

Änderungen in V1.2
------------------
- **Hauptursache "nur ein paar Leute werden zu Threads hinzugefügt" behoben:**
  `role.members` liefert nur *gecache* Mitglieder. Wenn der Bot den
  Privilegierten Gateway-Intent *SERVER MEMBERS INTENT* nicht aktiviert
  hat, schlug `guild.chunk()` bisher **stumm fehl** – und der Bot fügte
  nur die paar Mitglieder hinzu, die er durch recent Messages im Cache
  hatte. Neu gibt es `_ensure_guild_chunked`, das pro Guild einmalig
  eine klare Warnung ins Log UND in den Ticket-Log-Channel schreibt
  (genau erklärt, wie der Intent aktiviert wird).
- `_add_role_members_to_thread` komplett überarbeitet: Vorab
  `thread.fetch_members()` abrufen, damit bereits vorhandene Mitglieder
  *übersprungen* werden (spart Rate-Limit und vermeidet stumme
  'already in thread'-Fehler). `add_user` bekommt ein `discord.Object`,
  nicht den Member – schneller und klappt auch bei teilweisem Cache.
  Zähler für added/skipped/failed werden geloggt und von `syncroles`
  als Klartext-Zusammenfassung ausgegeben (z.B. "47 hinzugefügt,
  5 bereits vorhanden, 0 fehlgeschlagen").
- `_sync_member_to_open_tickets` (Rollenwechsel-/Beitritts-Listener)
  prüft vor jedem `add_user`, ob das Mitglied bereits im Thread ist.
- `_archive_ticket_thread`: "archiv-"-Präfix wird vorab abgeschnitten,
  wenn der Thread bereits damit beginnt – sonst entstünde bei erneutem
  Aufruf "archiv-archiv-...".
- `syncroles` gibt jetzt Klartext-Statistik über alle bearbeiteten Threads.
"""

from __future__ import annotations

import asyncio
import csv
import datetime
import html
import io
import logging
import re
import unicodedata
import uuid

import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

__version__ = "1.2.0"

log = logging.getLogger("red.supportcog")

# ---------------------------------------------------------------------------
# Konstanten & Helfer
# ---------------------------------------------------------------------------

TICKET_STATUS = {
    "ACTIVE": {"emoji": "🟢", "label": "Aktiv", "color": discord.Color.green()},
    "WAITING_USER": {"emoji": "🟡", "label": "Wartet auf Nutzer", "color": discord.Color.gold()},
    "WAITING_TEAM": {"emoji": "🔴", "label": "Wartet auf Team", "color": discord.Color.red()},
    "PAUSED": {"emoji": "⏸️", "label": "Pausiert", "color": discord.Color.dark_grey()},
}

HISTORY_LIMIT = 200
THREAD_AUTO_ARCHIVE_MINUTES = 10080  # 7 Tage


def _utcnow() -> datetime.datetime:
    """Zeitstempel (UTC, tz-aware) – überall im Cog verwenden."""
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_dt(value) -> datetime.datetime:
    """ISO-String oder datetime -> tz-aware datetime (robust, nie wirft)."""
    if isinstance(value, datetime.datetime):
        dt = value
    else:
        try:
            dt = datetime.datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return _utcnow()
    if dt.tzinfo is None:
        # Alte (naive) Einträge als UTC interpretieren
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _hours_since(value) -> float:
    return (_utcnow() - _parse_dt(value)).total_seconds() / 3600


def _fmt_duration(minutes) -> str:
    """Minuten -> '2 T 5 Std 12 Min'."""
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        minutes = 0
    if minutes < 1:
        return "<1 Min"
    days, remainder = divmod(minutes, 1440)
    hours, mins = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days} T")
    if hours:
        parts.append(f"{hours} Std")
    if mins or not parts:
        parts.append(f"{mins} Min")
    return " ".join(parts)


def _ticket_label(ticket: dict) -> str:
    number = ticket.get("number")
    if number:
        return f"#{int(number):04d}"
    return f"Kanal {ticket.get('channel_id')}"


_CUSTOM_EMOJI_RE = re.compile(r"^<a?:[A-Za-z0-9_~]+:\d+>$")
_EMOJI_MODIFIER_RE = re.compile(r"[\u200d\ufe0f\U0001F3FB-\U0001F3FF\u20e3]")


def _sanitize_emoji(value, default: str = "🎫") -> str:
    """Prüft, ob ein Wert als Emoji für Komponenten nutzbar ist."""
    if not value or not isinstance(value, str):
        return default
    cleaned = value.strip()
    if not cleaned:
        return default
    if _CUSTOM_EMOJI_RE.match(cleaned):
        return cleaned
    if len(cleaned) > 24:
        return default
    stripped = _EMOJI_MODIFIER_RE.sub("", cleaned)
    if not stripped:
        return cleaned if len(cleaned) <= 8 else default
    has_keycap = "\u20e3" in cleaned
    for char in stripped:
        category = unicodedata.category(char)
        if category == "Nd":
            if not has_keycap:
                return default
        elif category not in ("So", "Sk", "Mn", "Cf", "No"):
            return default
    return cleaned


def _escape_html(value) -> str:
    """HTML-Escaping über die Standardbibliothek.

    Hinweis: `discord.utils.escape_html` existiert in discord.py NICHT –
    die Vorgängerversionen sind daran (stillschweigend) gescheitert.
    """
    return html.escape(str(value))


async def _respond_error(interaction: discord.Interaction, message: str = "❌ Unerwarteter Fehler. Bitte versuche es erneut.") -> None:
    """Sichere Fehler-Antwort – funktioniert vor und nach dem Defer."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# HTML-Transkript-Vorlagen
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ticket #{ticket_number} - {channel_name}</title>
    <style>
        body {{ background-color: #313338; color: #dbdee1; font-family: 'gg sans', 'Noto Sans', Helvetica, Arial, sans-serif; padding: 20px; max-width: 900px; margin: 0 auto; }}
        .header {{ text-align: center; border-bottom: 2px solid #4e5058; padding-bottom: 20px; margin-bottom: 20px; }}
        .header h2 {{ color: #f2f3f5; margin-bottom: 5px; }}
        .meta {{ color: #949ba4; font-size: 0.9em; line-height: 1.6; }}
        .message {{ display: flex; margin-bottom: 15px; padding: 10px; border-radius: 8px; background-color: #2b2d31; }}
        .avatar {{ width: 40px; height: 40px; border-radius: 50%; margin-right: 15px; flex-shrink: 0; }}
        .content {{ flex: 1; min-width: 0; }}
        .author {{ font-weight: bold; color: #f2f3f5; margin-right: 10px; display: inline-block; }}
        .timestamp {{ color: #949ba4; font-size: 0.8em; }}
        .text {{ margin-top: 5px; word-wrap: break-word; white-space: pre-wrap; overflow-wrap: anywhere; }}
        .footer {{ text-align: center; color: #949ba4; font-size: 0.8em; margin-top: 30px; }}
    </style>
</head>
<body>
    <div class="header">
        <h2>Ticket #{ticket_number} &ndash; {channel_name}</h2>
        <div class="meta">
            Kategorie: {category}<br>
            Ersteller: {creator}<br>
            Erstellt am: {created_at} &bull; Geschlossen am: {closed_at} &bull; Dauer: {duration}<br>
            Geschlossen von: {closer}<br>
            Grund: {close_reason}<br>
            Nachrichten: {message_count}
        </div>
    </div>
    <div class="chat">
        {messages_html}
    </div>
    <div class="footer">Erstellt von SupportCog V{version}</div>
</body>
</html>"""

MESSAGE_HTML = """
<div class="message">
    <img class="avatar" src="{avatar_url}" alt="Avatar">
    <div class="content">
        <span class="author" style="color: {color}">{author}</span>
        <span class="timestamp">{timestamp}</span>
        <div class="text">{content}</div>
    </div>
</div>"""


# ---------------------------------------------------------------------------
# Ticket-Panel & Ticket-Interaktion (persistente Views)
# ---------------------------------------------------------------------------


class TicketPanelView(discord.ui.View):
    """Persistente View des Ticket-Panels (Dropdown zur Ticketerstellung)."""

    def __init__(self, cog: "SupportCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.select(
        placeholder="🎫 Wähle hier eine Kategorie für dein Ticket aus...",
        custom_id="support_ticket_create_select",
        min_values=1,
        max_values=1,
        options=[discord.SelectOption(label="Lädt...", value="loading")],
    )
    async def create_ticket_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        try:
            await self._handle(interaction, select)
        except Exception:
            log.exception("Fehler im Ticket-Panel")
            await _respond_error(interaction)

    async def _handle(self, interaction: discord.Interaction, select: discord.ui.Select):
        selected = select.values[0]
        if selected in ("loading", "none"):
            return await interaction.response.send_message(
                "⏳ Bitte warte noch einen Moment...", ephemeral=True
            )

        config = await self.cog.config.guild(interaction.guild).all()
        categories = config.get("categories", {})
        cat_id = selected
        if cat_id not in categories:
            return await interaction.response.send_message(
                "❌ Diese Kategorie existiert nicht mehr. Das Panel wird in Kürze aktualisiert.",
                ephemeral=True,
            )

        if interaction.user.id in (config.get("blacklist") or []):
            return await interaction.response.send_message(
                "❌ Du bist vom Ticket-System dieses Servers gesperrt.", ephemeral=True
            )

        active = config.get("active_tickets", [])
        user_tickets = [t for t in active if t.get("user_id") == interaction.user.id]
        max_tickets = config.get("max_tickets_per_user", 1)
        if len(user_tickets) >= max_tickets:
            return await interaction.response.send_message(
                f"❌ Du hast bereits das Maximum von {max_tickets} offenen Ticket(s) erreicht.",
                ephemeral=True,
            )

        cooldown_mins = config.get("cooldown_minutes", 0)
        if cooldown_mins > 0:
            last_created = None
            for ticket in user_tickets:
                stamp = _parse_dt(ticket.get("created_at"))
                if last_created is None or stamp > last_created:
                    last_created = stamp
            for entry in config.get("ticket_history", []):
                if entry.get("user_id") == interaction.user.id:
                    stamp = _parse_dt(entry.get("created_at"))
                    if last_created is None or stamp > last_created:
                        last_created = stamp
            if last_created is not None:
                minutes_since = (_utcnow() - last_created).total_seconds() / 60
                if minutes_since < cooldown_mins:
                    remaining = int(cooldown_mins - minutes_since) + 1
                    return await interaction.response.send_message(
                        f"❌ Cooldown aktiv. Bitte warte noch **{remaining} Minute(n)**.",
                        ephemeral=True,
                    )

        cat_data = categories.get(cat_id, {})
        max_cat_tickets = cat_data.get("max_tickets", 10)
        if max_cat_tickets > 0:
            active_count = sum(1 for t in active if t.get("cat_id") == cat_id)
            if active_count >= max_cat_tickets:
                return await interaction.response.send_message(
                    "❌ Diese Kategorie ist aktuell ausgelastet. Bitte versuche es später erneut.",
                    ephemeral=True,
                )

        await interaction.response.send_modal(TicketModal(self.cog, cat_id))


class TicketModal(discord.ui.Modal, title="🎫 Ticket erstellen"):
    """Modal zur Eingabe des Anliegens."""

    def __init__(self, cog: "SupportCog", cat_id: str):
        super().__init__()
        self.cog = cog
        self.cat_id = cat_id

    issue = discord.ui.TextInput(
        label="Was ist dein Anliegen?",
        style=discord.TextStyle.paragraph,
        required=True,
        min_length=10,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await self.cog.create_ticket(interaction, self.cat_id, self.issue.value)
        except Exception:
            log.exception("Fehler beim Erstellen des Tickets")
            await _respond_error(interaction, "❌ Das Ticket konnte nicht erstellt werden.")


class CloseTicketModal(discord.ui.Modal, title="🔒 Ticket schließen"):
    """Modal zur Eingabe des Schließ-Grunds."""

    def __init__(self, cog: "SupportCog"):
        super().__init__()
        self.cog = cog

    reason = discord.ui.TextInput(
        label="Grund für die Schließung",
        placeholder="Optional",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            await self.cog.close_ticket(
                interaction.channel,
                (self.reason.value or "").strip() or "Kein Grund angegeben",
                interaction.user,
                interaction=interaction,
            )
        except Exception:
            log.exception("Fehler beim Schließen des Tickets")
            await _respond_error(interaction, "❌ Das Ticket konnte nicht geschlossen werden.")


class TicketControlView(discord.ui.View):
    """Persistente Steuerungs-View in jedem Ticket-Kanal."""

    def __init__(self, cog: "SupportCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Übernehmen",
        custom_id="support_ticket_claim_btn",
        style=discord.ButtonStyle.success,
        emoji="✋",
    )
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.cog.claim_ticket(interaction, self)
        except Exception:
            log.exception("Fehler bei 'Übernehmen'")
            await _respond_error(interaction)

    @discord.ui.button(
        label="Eskalieren",
        custom_id="support_ticket_escalate_btn",
        style=discord.ButtonStyle.secondary,
        emoji="⚠️",
    )
    async def escalate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.cog.escalate_ticket(interaction, self)
        except Exception:
            log.exception("Fehler bei 'Eskalieren'")
            await _respond_error(interaction)

    @discord.ui.button(
        label="Schließen",
        custom_id="support_ticket_close_btn",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
    )
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            tickets = await self.cog.config.guild(interaction.guild).active_tickets()
            ticket = self.cog._find_ticket(tickets, interaction.channel.id)
            if ticket:
                is_creator = interaction.user.id == ticket.get("user_id")
                if not is_creator and not await self.cog.is_support(
                    interaction.user, interaction.guild, ticket
                ):
                    return await interaction.response.send_message(
                        "❌ Nur der Ticket-Ersteller und das Support-Team können dieses Ticket schließen.",
                        ephemeral=True,
                    )
            await interaction.response.send_modal(CloseTicketModal(self.cog))
        except Exception:
            log.exception("Fehler beim Schließen-Button")
            await _respond_error(interaction)

    @discord.ui.select(
        placeholder="Ticket-Status ändern...",
        custom_id="support_ticket_status_select",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="Aktiv", value="ACTIVE", emoji="🟢"),
            discord.SelectOption(label="Wartet auf User", value="WAITING_USER", emoji="🟡"),
            discord.SelectOption(label="Wartet auf Team", value="WAITING_TEAM", emoji="🔴"),
            discord.SelectOption(label="Pausiert", value="PAUSED", emoji="⏸️"),
        ],
    )
    async def status_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        try:
            await self.cog.change_status(interaction, select.values[0], self)
        except Exception:
            log.exception("Fehler beim Statuswechsel")
            await _respond_error(interaction)


class ReviewView(discord.ui.View):
    """Bewertungs-View nach dem Schließen (1-5 Sterne)."""

    def __init__(self, cog: "SupportCog", ticket_data: dict):
        super().__init__(timeout=180)
        self.cog = cog
        self.ticket_data = ticket_data
        self.message: discord.Message | None = None
        for stars in range(1, 6):
            button = discord.ui.Button(
                label=str(stars),
                emoji="⭐",
                custom_id=f"review_star_{stars}",
                style=discord.ButtonStyle.secondary,
            )
            button.callback = self.review_stars
            self.add_item(button)

    async def review_stars(self, interaction: discord.Interaction):
        if interaction.user.id != self.ticket_data.get("user_id"):
            return await interaction.response.send_message(
                "❌ Nur der Ticket-Ersteller kann bewerten.", ephemeral=True
            )
        try:
            stars = int(str(interaction.data.get("custom_id", "0"))[-1])
        except ValueError:
            stars = 0
        stars = max(1, min(stars, 5))
        self.stop()
        await interaction.response.edit_message(
            content=f"✅ Danke für dein Feedback ({stars}⭐)! Das Ticket wird jetzt geschlossen.",
            view=None,
        )
        try:
            await self.cog.delete_ticket_channel(interaction.channel, self.ticket_data, stars)
        except Exception:
            log.exception("Fehler nach der Bewertung")

    async def on_timeout(self):
        if not self.message:
            return
        try:
            await self.message.channel.send(
                "⏰ Keine Bewertung eingegangen – das Ticket wird jetzt geschlossen."
            )
        except Exception:
            pass
        try:
            await self.cog.delete_ticket_channel(self.message.channel, self.ticket_data, 0)
        except Exception:
            log.exception("Timeout-Aufräumen fehlgeschlagen")


# ---------------------------------------------------------------------------
# Setup-Views (Basis-Setup & Kategorie-Setup)
# ---------------------------------------------------------------------------


class SimpleNumberModal(discord.ui.Modal):
    """Modal für einfache Zahleneingaben im Setup."""

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
            max_length=10,
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = int(str(self.input.value).strip())
        except ValueError:
            return await interaction.response.send_message(
                "❌ Bitte gib eine gültige Zahl ein.", ephemeral=True
            )
        if not (self.min_val <= value <= self.max_val):
            return await interaction.response.send_message(
                f"❌ Der Wert muss zwischen {self.min_val} und {self.max_val} liegen.",
                ephemeral=True,
            )
        setattr(self.wizard, self.attr_name, value)
        self.wizard._update_labels()
        await interaction.response.edit_message(view=self.wizard)


class CategoryAllTextModal(discord.ui.Modal, title="Kategorie-Texte"):
    """Modal: Name, Beschreibung, Abkürzung und Emoji einer Kategorie."""

    def __init__(self, wizard: "CategorySetupView"):
        super().__init__()
        self.wizard = wizard
        self.name_input = discord.ui.TextInput(
            label="Name", default=wizard.name or "", max_length=50, required=True
        )
        self.desc_input = discord.ui.TextInput(
            label="Beschreibung", default=wizard.description or "", max_length=100, required=False
        )
        self.abbr_input = discord.ui.TextInput(
            label="Abkürzung (z. B. SUP)", default=wizard.abbr or "", max_length=10, required=True
        )
        self.emoji_input = discord.ui.TextInput(
            label="Emoji", default=wizard.emoji or "🎫", max_length=32, required=False
        )
        self.add_item(self.name_input)
        self.add_item(self.desc_input)
        self.add_item(self.abbr_input)
        self.add_item(self.emoji_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.wizard.name = self.name_input.value.strip()
        self.wizard.description = (self.desc_input.value or "").strip() or None
        self.wizard.abbr = self.abbr_input.value.strip().upper() or "TICKET"
        raw_emoji = (self.emoji_input.value or "").strip() or "🎫"
        safe_emoji = _sanitize_emoji(raw_emoji)
        self.wizard.emoji = safe_emoji
        self.wizard._update_labels()
        await interaction.response.edit_message(view=self.wizard)
        if raw_emoji != safe_emoji:
            await interaction.followup.send(
                f"⚠️ '{raw_emoji[:50]}' ist kein gültiges Emoji – es wird '{safe_emoji}' verwendet.",
                ephemeral=True,
            )


class CategorySetupView(discord.ui.View):
    """Interaktive Erstellung/Bearbeitung einer Ticket-Kategorie."""

    def __init__(
        self,
        cog: "SupportCog",
        ctx: commands.Context,
        cat_id: str | None = None,
        cat_data: dict | None = None,
    ):
        super().__init__(timeout=300)
        self.cog = cog
        self.ctx = ctx
        self.cat_id = cat_id
        cat_data = cat_data or {}

        self.name = cat_data.get("name") or ""
        self.description = cat_data.get("description") or ""
        self.emoji = cat_data.get("emoji") or "🎫"
        self.abbr = cat_data.get("abbr") or "TICKET"
        self.discord_category_id = cat_data.get("discord_category_id")
        self.thread_parent_id = cat_data.get("thread_parent_id")
        self.staff_role_id = cat_data.get("staff_role_id")
        self.high_team_role_id = cat_data.get("high_team_role_id")
        self.max_tickets = cat_data.get("max_tickets", 10)
        if self.max_tickets is None:
            self.max_tickets = 10

        self._build_ui()
        self._update_labels()

    def _build_ui(self):
        guild = self.ctx.guild

        self.btn_texts = discord.ui.Button(
            label="Texte anpassen", style=discord.ButtonStyle.primary, row=0, emoji="📝"
        )
        self.btn_texts.callback = self._texts_cb
        self.add_item(self.btn_texts)

        self.btn_max_tickets = discord.ui.Button(
            label=f"Max aktiv: {self.max_tickets}", style=discord.ButtonStyle.secondary,
            emoji="📊", row=0,
        )
        self.btn_max_tickets.callback = self._max_tickets_cb
        self.add_item(self.btn_max_tickets)

        self.btn_save = discord.ui.Button(
            label="Speichern", style=discord.ButtonStyle.success, emoji="✅", row=0
        )
        self.btn_save.callback = self._save_cb
        self.add_item(self.btn_save)

        cat_options = [
            discord.SelectOption(label=cat.name[:100], value=str(cat.id))
            for cat in guild.categories[:25]
        ]
        if not cat_options:
            cat_options = [discord.SelectOption(label="Keine Kategorien", value="none")]
        self.disc_cat_sel = discord.ui.Select(
            placeholder=self._placeholder_for("discord_category_id", "Discord Kategorie"),
            options=cat_options,
            row=1,
        )
        self.disc_cat_sel.callback = self._disc_cat_cb
        self.add_item(self.disc_cat_sel)

        thread_options = [
            discord.SelectOption(label=f"#{c.name}"[:100], value=str(c.id))
            for c in guild.text_channels[:25]
        ]
        if not thread_options:
            thread_options = [discord.SelectOption(label="Keine Textkanäle", value="none")]
        self.thread_sel = discord.ui.Select(
            placeholder=self._placeholder_for("thread_parent_id", "Thread-Channel"),
            options=thread_options,
            row=2,
        )
        self.thread_sel.callback = self._thread_cb
        self.add_item(self.thread_sel)

        staff_options = [
            discord.SelectOption(label=role.name[:100], value=str(role.id))
            for role in guild.roles
            if not role.managed
        ][:25]
        if not staff_options:
            staff_options = [discord.SelectOption(label="Keine Rollen", value="none")]
        self.staff_sel = discord.ui.Select(
            placeholder=self._role_placeholder("staff_role_id", "Support-Rolle"),
            options=staff_options,
            row=3,
        )
        self.staff_sel.callback = self._staff_cb
        self.add_item(self.staff_sel)

        self.high_sel = discord.ui.Select(
            placeholder=self._role_placeholder("high_team_role_id", "High-Team Rolle (optional)"),
            options=list(staff_options),
            row=4,
        )
        self.high_sel.callback = self._high_cb
        self.add_item(self.high_sel)

    def _placeholder_for(self, attr: str, default: str) -> str:
        channel_id = getattr(self, attr)
        if channel_id:
            channel = self.ctx.guild.get_channel(channel_id)
            if channel:
                return f"{default}: {channel.name}"[:150]
        return default

    def _role_placeholder(self, attr: str, default: str) -> str:
        role_id = getattr(self, attr)
        if role_id:
            role = self.ctx.guild.get_role(role_id)
            if role:
                return f"{default}: {role.name}"[:150]
        return default

    def _update_labels(self):
        self.btn_texts.label = f"Name: {self.name}" if self.name else "Texte anpassen"
        self.btn_texts.label = self.btn_texts.label[:80]
        self.btn_max_tickets.label = f"Max aktiv: {self.max_tickets}"

    async def _texts_cb(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CategoryAllTextModal(self))

    async def _max_tickets_cb(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            SimpleNumberModal(self, "max_tickets", "Maximale aktive Tickets", 0, 100)
        )

    async def _disc_cat_cb(self, interaction: discord.Interaction):
        if self.disc_cat_sel.values[0] != "none":
            self.discord_category_id = int(self.disc_cat_sel.values[0])
            self.thread_parent_id = None
        else:
            self.discord_category_id = None
        self._refresh_placeholders()
        await interaction.response.edit_message(view=self)

    async def _thread_cb(self, interaction: discord.Interaction):
        if self.thread_sel.values[0] != "none":
            self.thread_parent_id = int(self.thread_sel.values[0])
            self.discord_category_id = None
        else:
            self.thread_parent_id = None
        self._refresh_placeholders()
        await interaction.response.edit_message(view=self)

    async def _staff_cb(self, interaction: discord.Interaction):
        if self.staff_sel.values[0] != "none":
            self.staff_role_id = int(self.staff_sel.values[0])
        else:
            self.staff_role_id = None
        self._refresh_placeholders()
        await interaction.response.edit_message(view=self)

    async def _high_cb(self, interaction: discord.Interaction):
        if self.high_sel.values[0] != "none":
            self.high_team_role_id = int(self.high_sel.values[0])
        else:
            self.high_team_role_id = None
        self._refresh_placeholders()
        await interaction.response.edit_message(view=self)

    def _refresh_placeholders(self):
        self.disc_cat_sel.placeholder = self._placeholder_for(
            "discord_category_id", "Discord Kategorie"
        )
        self.thread_sel.placeholder = self._placeholder_for("thread_parent_id", "Thread-Channel")
        self.staff_sel.placeholder = self._role_placeholder("staff_role_id", "Support-Rolle")
        self.high_sel.placeholder = self._role_placeholder(
            "high_team_role_id", "High-Team Rolle (optional)"
        )

    async def _save_cb(self, interaction: discord.Interaction):
        if not self.name or not self.abbr or not self.staff_role_id:
            return await interaction.response.send_message(
                "❌ Bitte lege mindestens Name, Abkürzung und eine Support-Rolle fest.",
                ephemeral=True,
            )
        if not self.discord_category_id and not self.thread_parent_id:
            return await interaction.response.send_message(
                "❌ Bitte wähle eine Discord-Kategorie ODER einen Thread-Channel aus.",
                ephemeral=True,
            )
        await self.cog.save_category(interaction, self, self.cat_id)
        self.stop()


class BaseSetupView(discord.ui.View):
    """Interaktive Basis-Konfiguration des Ticket-Systems."""

    def __init__(self, cog: "SupportCog", ctx: commands.Context, conf: dict | None = None):
        super().__init__(timeout=300)
        self.cog = cog
        self.ctx = ctx
        conf = conf or {}

        self.log_channel_id = conf.get("log_channel_id")
        self.admin_role_id = conf.get("admin_role_id")
        self.dm_notifications = conf.get("dm_notifications", True)
        self.autoclose_hours = conf.get("autoclose_hours", 48)
        self.cooldown_minutes = conf.get("cooldown_minutes", 0)
        self.max_tickets_per_user = conf.get("max_tickets_per_user", 1)
        self.delete_threads_after_close = conf.get("delete_threads_after_close", False)
        self.auto_escalate_hours = conf.get("auto_escalate_hours", 0)
        self.show_category_stats = conf.get("show_category_stats", True)
        self.use_emoji_charts = conf.get("use_emoji_charts", True)

        self._build_ui()
        self._update_labels()

    def _build_ui(self):
        guild = self.ctx.guild

        log_options = [
            discord.SelectOption(label=f"#{c.name}"[:100], value=str(c.id))
            for c in guild.text_channels[:25]
        ]
        if not log_options:
            log_options = [discord.SelectOption(label="Keine Textkanäle", value="none")]
        if self.log_channel_id:
            channel = guild.get_channel(self.log_channel_id)
            if channel:
                log_options.insert(
                    0, discord.SelectOption(label=f"Aktuell: #{channel.name}"[:100], value=str(channel.id))
                )
        self.log_sel = discord.ui.Select(
            placeholder=self._channel_placeholder(self.log_channel_id, "Log-Channel wählen"),
            options=log_options,
            row=0,
        )
        self.log_sel.callback = self._log_select_cb
        self.add_item(self.log_sel)

        admin_options = [
            discord.SelectOption(label=role.name[:100], value=str(role.id))
            for role in guild.roles
            if not role.managed
        ][:25]
        if not admin_options:
            admin_options = [discord.SelectOption(label="Keine Rollen", value="none")]
        self.admin_sel = discord.ui.Select(
            placeholder=self._role_placeholder(
                self.admin_role_id, "Admin-Rolle (sieht ALLE Tickets, z. B. Inhaber)"
            ),
            options=admin_options,
            row=1,
        )
        self.admin_sel.callback = self._admin_select_cb
        self.add_item(self.admin_sel)

        self.btn_dm = discord.ui.Button(
            label="DMs: AN", style=discord.ButtonStyle.success, emoji="✉️", row=2
        )
        self.btn_dm.callback = self._dm_toggle_cb
        self.add_item(self.btn_dm)

        self.btn_auto = discord.ui.Button(
            label=f"Auto-Close: {self.autoclose_hours}h",
            style=discord.ButtonStyle.secondary,
            emoji="⏳",
            row=2,
        )
        self.btn_auto.callback = self._auto_cb
        self.add_item(self.btn_auto)

        self.btn_cool = discord.ui.Button(
            label=f"Cooldown: {self.cooldown_minutes}m",
            style=discord.ButtonStyle.secondary,
            emoji="❄️",
            row=2,
        )
        self.btn_cool.callback = self._cool_cb
        self.add_item(self.btn_cool)

        self.btn_max = discord.ui.Button(
            label=f"Max Tickets: {self.max_tickets_per_user}",
            style=discord.ButtonStyle.secondary,
            emoji="🔢",
            row=2,
        )
        self.btn_max.callback = self._max_cb
        self.add_item(self.btn_max)

        self.btn_del_thread = discord.ui.Button(
            label="Threads löschen: AUS",
            style=discord.ButtonStyle.danger,
            emoji="🗑️",
            row=3,
        )
        self.btn_del_thread.callback = self._del_thread_toggle_cb
        self.add_item(self.btn_del_thread)

        self.btn_esc = discord.ui.Button(
            label=f"Auto-Eskalation: {self.auto_escalate_hours}h",
            style=discord.ButtonStyle.secondary,
            emoji="🚨",
            row=3,
        )
        self.btn_esc.callback = self._esc_cb
        self.add_item(self.btn_esc)

        self.btn_catstats = discord.ui.Button(
            label="Kategorie-Statistiken: AN",
            style=discord.ButtonStyle.success,
            emoji="📊",
            row=3,
        )
        self.btn_catstats.callback = self._catstats_toggle_cb
        self.add_item(self.btn_catstats)

        self.btn_emoji = discord.ui.Button(
            label="Emoji-Balken: AN",
            style=discord.ButtonStyle.success,
            emoji="📈",
            row=3,
        )
        self.btn_emoji.callback = self._emoji_toggle_cb
        self.add_item(self.btn_emoji)

        self.btn_finish = discord.ui.Button(
            label="Setup abschließen", style=discord.ButtonStyle.success, emoji="✅", row=4
        )
        self.btn_finish.callback = self._finish_cb
        self.add_item(self.btn_finish)

    def _channel_placeholder(self, channel_id, default: str) -> str:
        if channel_id:
            channel = self.ctx.guild.get_channel(channel_id)
            if channel:
                return f"{default.split(' wählen')[0]}: #{channel.name}"[:150]
        return default

    def _role_placeholder(self, role_id, default: str) -> str:
        if role_id:
            role = self.ctx.guild.get_role(role_id)
            if role:
                return f"{default.split(' (')[0]}: {role.name}"[:150]
        return default

    def _update_labels(self):
        self.btn_dm.label = f"DMs: {'AN' if self.dm_notifications else 'AUS'}"
        self.btn_dm.style = (
            discord.ButtonStyle.success if self.dm_notifications else discord.ButtonStyle.danger
        )
        self.btn_auto.label = f"Auto-Close: {self.autoclose_hours}h"
        self.btn_cool.label = f"Cooldown: {self.cooldown_minutes}m"
        self.btn_max.label = f"Max Tickets: {self.max_tickets_per_user}"
        self.btn_del_thread.label = f"Threads löschen: {'AN' if self.delete_threads_after_close else 'AUS'}"
        self.btn_del_thread.style = (
            discord.ButtonStyle.success
            if self.delete_threads_after_close
            else discord.ButtonStyle.danger
        )
        self.btn_esc.label = f"Auto-Eskalation: {self.auto_escalate_hours}h"
        self.btn_catstats.label = f"Kategorie-Statistiken: {'AN' if self.show_category_stats else 'AUS'}"
        self.btn_catstats.style = (
            discord.ButtonStyle.success if self.show_category_stats else discord.ButtonStyle.danger
        )
        self.btn_emoji.label = f"Emoji-Balken: {'AN' if self.use_emoji_charts else 'AUS'}"
        self.btn_emoji.style = (
            discord.ButtonStyle.success if self.use_emoji_charts else discord.ButtonStyle.danger
        )

    async def _log_select_cb(self, interaction: discord.Interaction):
        if self.log_sel.values[0] != "none":
            self.log_channel_id = int(self.log_sel.values[0])
        else:
            self.log_channel_id = None
        self.log_sel.placeholder = self._channel_placeholder(self.log_channel_id, "Log-Channel wählen")
        await interaction.response.edit_message(view=self)

    async def _admin_select_cb(self, interaction: discord.Interaction):
        if self.admin_sel.values[0] != "none":
            self.admin_role_id = int(self.admin_sel.values[0])
        else:
            self.admin_role_id = None
        self.admin_sel.placeholder = self._role_placeholder(
            self.admin_role_id, "Admin-Rolle (sieht ALLE Tickets, z. B. Inhaber)"
        )
        await interaction.response.edit_message(view=self)

    async def _dm_toggle_cb(self, interaction: discord.Interaction):
        self.dm_notifications = not self.dm_notifications
        self._update_labels()
        await interaction.response.edit_message(view=self)

    async def _auto_cb(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            SimpleNumberModal(self, "autoclose_hours", "Auto-Close (Stunden, 0=aus)", 0, 500)
        )

    async def _cool_cb(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            SimpleNumberModal(self, "cooldown_minutes", "Cooldown (Minuten, 0=aus)", 0, 10080)
        )

    async def _max_cb(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            SimpleNumberModal(self, "max_tickets_per_user", "Max Tickets pro User", 1, 10)
        )

    async def _del_thread_toggle_cb(self, interaction: discord.Interaction):
        self.delete_threads_after_close = not self.delete_threads_after_close
        self._update_labels()
        await interaction.response.edit_message(view=self)

    async def _esc_cb(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            SimpleNumberModal(self, "auto_escalate_hours", "Auto-Eskalation nach Stunden (0=aus)", 0, 500)
        )

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
            return await interaction.response.send_message(
                "❌ Bitte wähle zuerst einen Log-Channel aus!", ephemeral=True
            )
        await self.cog.finish_base_setup(interaction, self)
        self.stop()


# ---------------------------------------------------------------------------
# Der Cog
# ---------------------------------------------------------------------------


class SupportCog(commands.Cog):
    """Professionelles Ticket-System mit Kategorien, Threads, Statistiken und Automatisierung."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=98765432123456789, force_registration=True)
        self.config.register_guild(
            panels=[],
            log_channel_id=None,
            admin_role_id=None,
            dm_notifications=True,
            categories={},
            active_tickets=[],
            autoclose_hours=48,
            cooldown_minutes=0,
            blacklist=[],
            stats={},
            max_tickets_per_user=1,
            total_tickets_created=0,
            delete_threads_after_close=False,
            auto_escalate_hours=0,
            show_category_stats=True,
            use_emoji_charts=True,
            category_stats={},
            ticket_history=[],
            ticket_counter=0,
            last_daily_summary=None,
            last_weekly_summary=None,
        )
        self._active_channel_cache: dict[int, set[int]] = {}
        self._init_task: asyncio.Task | None = None
        self._autoclose_task: asyncio.Task | None = None
        self._summary_task: asyncio.Task | None = None
        # Pro Guild einmalig vor dem nächsten Neustart warnen, wenn der
        # Members-Intent fehlt – verhindert Spam im Log-Channel.
        self._warned_chunk_guilds: set[int] = set()

    async def cog_load(self):
        self._init_task = asyncio.create_task(self._async_init())
        self._summary_task = asyncio.create_task(self.summary_loop())

    def cog_unload(self):
        for task in (self._init_task, self._autoclose_task, self._summary_task):
            if task and not task.done():
                task.cancel()

    async def _async_init(self):
        try:
            await self.bot.wait_until_ready()
            await self._initialize_views_and_cache()
            self._autoclose_task = asyncio.create_task(self.autoclose_loop())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("SupportCog-Initialisierung fehlgeschlagen")

    async def _initialize_views_and_cache(self):
        for guild_id, data in (await self.config.all_guilds()).items():
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            active = data.get("active_tickets", [])
            self._active_channel_cache[guild_id] = {
                t.get("channel_id") for t in active if t.get("channel_id")
            }
            categories = data.get("categories", {})

            for panel in data.get("panels", []):
                try:
                    view = self._make_panel_view(categories, active)
                    self.bot.add_view(view, message_id=panel["msg_id"])
                    try:
                        channel = guild.get_channel(panel.get("channel_id"))
                        if channel:
                            message = await channel.fetch_message(panel["msg_id"])
                            embed = await self._build_panel_embed(guild, categories, active)
                            await message.edit(embed=embed, view=view)
                    except discord.HTTPException:
                        pass
                except Exception:
                    log.exception("Panel-Registrierung fehlgeschlagen")

            for ticket in active:
                if not ticket.get("panel_msg_id"):
                    continue
                try:
                    view = TicketControlView(self)
                    self._apply_ticket_state_to_view(view, ticket)
                    self.bot.add_view(view, message_id=ticket["panel_msg_id"])
                except Exception:
                    log.exception("Control-View-Registrierung fehlgeschlagen")

    def _apply_ticket_state_to_view(self, view: TicketControlView, ticket: dict):
        claimed = bool(ticket.get("claimed_by"))
        escalated = bool(ticket.get("escalated"))
        for child in view.children:
            custom_id = getattr(child, "custom_id", None)
            if custom_id == "support_ticket_claim_btn":
                child.label = "Freigeben" if claimed else "Übernehmen"
                child.style = (
                    discord.ButtonStyle.secondary if claimed else discord.ButtonStyle.success
                )
            elif custom_id == "support_ticket_escalate_btn" and escalated:
                child.disabled = True

    # -- Cache- & Ticket-Helfer ---------------------------------------------

    def _add_to_active_cache(self, guild_id: int, channel_id: int):
        self._active_channel_cache.setdefault(guild_id, set()).add(channel_id)

    def _remove_from_active_cache(self, guild_id: int, channel_id: int):
        if guild_id in self._active_channel_cache:
            self._active_channel_cache[guild_id].discard(channel_id)

    @staticmethod
    def _find_ticket(tickets, channel_id: int):
        for ticket in tickets or []:
            if ticket.get("channel_id") == channel_id:
                return ticket
        return None

    async def _mutate_ticket(self, guild: discord.Guild, channel_id: int, **updates) -> bool:
        """Aktualisiert Felder eines aktiven Tickets (frisch gelesen, kein Überschreiben)."""
        tickets = await self.config.guild(guild).active_tickets()
        ticket = self._find_ticket(tickets, channel_id)
        if ticket is None:
            return False
        ticket.update(updates)
        await self.config.guild(guild).active_tickets.set(tickets)
        return True

    async def _remove_ticket(self, guild: discord.Guild, channel_id: int):
        """Entfernt ein aktives Ticket aus Config & Cache (idempotent)."""
        tickets = await self.config.guild(guild).active_tickets()
        removed = self._find_ticket(tickets, channel_id)
        if removed is None:
            self._remove_from_active_cache(guild.id, channel_id)
            return None
        tickets = [t for t in tickets if t.get("channel_id") != channel_id]
        await self.config.guild(guild).active_tickets.set(tickets)
        self._remove_from_active_cache(guild.id, channel_id)
        return removed

    # -- Benachrichtigungen --------------------------------------------------

    async def send_dm(self, user, title: str, description: str):
        if user is None:
            return
        try:
            embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
            await user.send(embed=embed)
        except Exception:
            pass

    async def send_log(self, guild: discord.Guild, title: str, color, fields):
        try:
            log_channel_id = await self.config.guild(guild).log_channel_id()
            if not log_channel_id:
                return
            log_channel = guild.get_channel(log_channel_id)
            if not log_channel:
                return
            embed = discord.Embed(title=title, color=color, timestamp=_utcnow())
            for name, value in fields:
                embed.add_field(name=str(name)[:256], value=str(value)[:1024], inline=False)
            await log_channel.send(embed=embed)
        except Exception:
            log.exception("Log senden fehlgeschlagen")

    @staticmethod
    def _emoji_bar(value: int, max_value: int, length: int = 10) -> str:
        if max_value <= 0:
            return "⬜" * length
        filled = int((value / max_value) * length)
        filled = max(0, min(filled, length))
        return "🟩" * filled + "⬜" * (length - filled)

    # -- Zusammenfassungs-Loop ------------------------------------------------

    async def summary_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                now = _utcnow()
                today = now.date().isoformat()
                for guild_id in await self.config.all_guilds():
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        continue
                    guild_conf = self.config.guild(guild)
                    last_daily = await guild_conf.last_daily_summary()
                    if last_daily is None:
                        # Grundzustand initialisieren, ohne sofort zu senden
                        await guild_conf.last_daily_summary.set(today)
                    elif last_daily != today:
                        await guild_conf.last_daily_summary.set(today)
                        await self.send_summary(guild, "daily")
                    if now.weekday() == 0:
                        last_weekly = await guild_conf.last_weekly_summary()
                        if last_weekly is None:
                            await guild_conf.last_weekly_summary.set(today)
                        elif last_weekly != today:
                            await guild_conf.last_weekly_summary.set(today)
                            await self.send_summary(guild, "weekly")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Summary-Loop Fehler")
            await asyncio.sleep(60)

    async def send_summary(self, guild: discord.Guild, period: str):
        try:
            log_channel_id = await self.config.guild(guild).log_channel_id()
            if not log_channel_id:
                return
            log_channel = guild.get_channel(log_channel_id)
            if not log_channel:
                return
            conf = await self.config.guild(guild).all()
            history = conf.get("ticket_history", [])
            active_tickets = conf.get("active_tickets", [])
            stats = conf.get("stats", {})

            if period == "daily":
                delta = datetime.timedelta(days=1)
                title = "📅 Tägliche Ticket-Zusammenfassung"
            else:
                delta = datetime.timedelta(days=7)
                title = "📊 Wöchentliche Ticket-Zusammenfassung"

            now = _utcnow()
            start = now - delta
            created = 0
            closed = 0
            for entry in history:
                if _parse_dt(entry.get("created_at")) >= start:
                    created += 1
                if entry.get("closed_at") and _parse_dt(entry.get("closed_at")) >= start:
                    closed += 1

            total_closed_all = sum(u.get("closed", 0) for u in stats.values())
            total_reaction = sum(u.get("total_reaction_minutes", 0) for u in stats.values())
            reaction_count = sum(u.get("reaction_count", 0) for u in stats.values())
            avg_reaction = total_reaction / reaction_count if reaction_count else 0
            total_duration = sum(u.get("total_duration_minutes", 0) for u in stats.values())
            duration_count = sum(u.get("ticket_count", 0) for u in stats.values())
            avg_duration = total_duration / duration_count if duration_count else 0

            embed = discord.Embed(title=title, color=discord.Color.gold(), timestamp=now)
            embed.add_field(name="Erstellt", value=str(created), inline=True)
            embed.add_field(name="Geschlossen", value=str(closed), inline=True)
            embed.add_field(name="Offen", value=str(len(active_tickets)), inline=True)
            embed.add_field(
                name="Ø Erste Reaktion (gesamt)",
                value=f"{avg_reaction:.1f} Min" if reaction_count else "—",
                inline=True,
            )
            embed.add_field(
                name="Ø Bearbeitungsdauer (gesamt)",
                value=_fmt_duration(avg_duration) if duration_count else "—",
                inline=True,
            )
            embed.add_field(name="Gesamt geschlossen", value=str(total_closed_all), inline=True)
            await log_channel.send(embed=embed)
        except Exception:
            log.exception("Zusammenfassung senden fehlgeschlagen")

    # -- Auto-Close & Auto-Eskalation ------------------------------------------

    async def autoclose_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                for guild_id, data in (await self.config.all_guilds()).items():
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        continue
                    autoclose_hours = data.get("autoclose_hours", 48) or 0
                    escalate_hours = data.get("auto_escalate_hours", 0) or 0
                    for ticket in list(data.get("active_tickets", [])):
                        channel_id = ticket.get("channel_id")
                        channel = guild.get_channel_or_thread(channel_id) if channel_id else None
                        if channel is None and channel_id:
                            # Cache verfehlt den Kanal (z. B. archivierter Thread nach
                            # Neustart) -> per API nachschlagen, erst dann gilt er als weg
                            try:
                                channel = await guild.fetch_channel(channel_id)
                            except discord.NotFound:
                                await self._remove_ticket(guild, channel_id)
                                continue
                            except discord.HTTPException:
                                continue
                        if channel is None:
                            continue
                        status = ticket.get("status", "ACTIVE")

                        # Offene Ticket-Threads heilen: versehentlich Archivierte
                        # öffnen und lange Auto-Archive-Dauer sicherstellen
                        if isinstance(channel, discord.Thread):
                            try:
                                if channel.archived:
                                    await channel.edit(archived=False)
                                if channel.auto_archive_duration != THREAD_AUTO_ARCHIVE_MINUTES:
                                    await channel.edit(
                                        auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES
                                    )
                            except discord.HTTPException:
                                pass

                        if (
                            status == "WAITING_TEAM"
                            and escalate_hours > 0
                            and not ticket.get("escalated")
                        ):
                            idle = _hours_since(ticket.get("last_message") or ticket.get("created_at"))
                            if idle >= escalate_hours:
                                await self.auto_escalate_ticket(guild, ticket, channel)
                                await self._mutate_ticket(
                                    guild, channel_id, escalated=True, last_message=_utcnow().isoformat()
                                )
                                ticket["escalated"] = True

                        if status in ("WAITING_TEAM", "PAUSED"):
                            continue
                        if autoclose_hours <= 0:
                            continue

                        idle_hours = _hours_since(ticket.get("last_message") or ticket.get("created_at"))
                        warn_after = (autoclose_hours - 2) if autoclose_hours > 2 else (autoclose_hours / 2)
                        if idle_hours >= warn_after and not ticket.get("warned"):
                            try:
                                remaining = max(autoclose_hours - idle_hours, 0)
                                await channel.send(
                                    f"⚠️ <@{ticket.get('user_id')}>, dieses Ticket wird in ca. "
                                    f"**{int(remaining) + 1} Stunde(n)** automatisch geschlossen, "
                                    "wenn keine Aktivität mehr erfolgt."
                                )
                                await self._mutate_ticket(guild, channel_id, warned=True)
                                ticket["warned"] = True
                            except discord.HTTPException:
                                pass
                        if idle_hours >= autoclose_hours:
                            await self.close_ticket(
                                channel, "Inaktivität (Auto-Close)", guild.me, is_auto=True
                            )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Auto-Close-Loop Fehler")
            await asyncio.sleep(300)

    async def auto_escalate_ticket(self, guild: discord.Guild, ticket: dict, channel):
        conf = await self.config.guild(guild).all()
        cat_data = conf.get("categories", {}).get(ticket.get("cat_id"), {}) or {}
        high_role_id = cat_data.get("high_team_role_id")
        if not high_role_id:
            return
        high_role = guild.get_role(high_role_id)
        mention = high_role.mention if high_role else "@High-Team"
        try:
            await channel.send(f"🚨 **Automatische Eskalation:** {mention} wurde benachrichtigt.")
        except discord.HTTPException:
            pass
        # Zugang für das High-Team sicherstellen
        try:
            if isinstance(channel, discord.Thread) and high_role:
                await self._add_role_members_to_thread(channel, [high_role])
            elif high_role:
                await channel.set_permissions(
                    high_role,
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                )
        except discord.HTTPException:
            log.warning("Konnte High-Team-Zugang bei Eskalation nicht setzen.")
        await self.send_log(
            guild,
            "🚨 Ticket automatisch eskaliert",
            discord.Color.orange(),
            [("Ticket", _ticket_label(ticket)), ("Grund", "Keine Reaktion im Status 'Wartet auf Team'")],
        )
        if conf.get("dm_notifications"):
            await self.send_dm(
                guild.get_member(ticket.get("user_id")),
                "🚨 Dein Ticket wurde eskaliert",
                "Dein Ticket wurde automatisch an das High-Team eskaliert.",
            )

    # -- Listener ---------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if message.channel.id not in self._active_channel_cache.get(message.guild.id, set()):
            return
        if not isinstance(message.author, discord.Member):
            return
        try:
            tickets = await self.config.guild(message.guild).active_tickets()
            ticket = self._find_ticket(tickets, message.channel.id)
            if ticket is None:
                return

            if ticket.get("status", "ACTIVE") == "PAUSED":
                allowed_roles = {
                    ticket.get("staff_role_id"),
                    ticket.get("high_team_role_id"),
                }
                is_allowed = message.author.guild_permissions.manage_messages or any(
                    role.id in allowed_roles for role in message.author.roles
                )
                if not is_allowed:
                    try:
                        await message.delete()
                        await message.channel.send("⏸️ Dieses Ticket ist pausiert.", delete_after=5)
                    except discord.HTTPException:
                        pass
                    return

            ticket["last_message"] = _utcnow().isoformat()
            ticket["warned"] = False
            if not ticket.get("first_response_at") and message.author.id != ticket.get("user_id"):
                staff_role_id = ticket.get("staff_role_id")
                is_staff = (
                    staff_role_id is None
                    or message.author.guild_permissions.manage_guild
                    or any(r.id == staff_role_id for r in message.author.roles)
                )
                if is_staff:
                    ticket["first_response_at"] = _utcnow().isoformat()
                    ticket["first_responder_id"] = message.author.id
            await self.config.guild(message.guild).active_tickets.set(tickets)
        except Exception:
            log.exception("Fehler in on_message (Ticket-Tracking)")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if isinstance(channel, discord.Thread):
            return  # Threads behandelt on_thread_delete
        await self._cleanup_deleted_ticket_channel(channel)

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread):
        await self._cleanup_deleted_ticket_channel(thread)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Neu hinzugefügte Rollen -> automatisch zu offenen Ticket-Threads hinzufügen.

        Stellt sicher, dass Support-/High-Team-/Admin-Rollen die Tickets ihrer
        Kategorien immer sehen – auch Mitglieder, die die Rolle erst NACH dem
        Öffnen des Tickets erhalten haben.
        """
        if before.roles == after.roles:
            return
        before_ids = {role.id for role in before.roles}
        added = {role.id for role in after.roles} - before_ids
        if not added:
            return
        try:
            await self._sync_member_to_open_tickets(after, added)
        except Exception:
            log.exception("Member-Sync (Rollenwechsel) fehlgeschlagen")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Serverbeitritt mit Support-/High-Team-/Admin-Rolle -> Zugang zu offenen Tickets."""
        try:
            role_ids = {role.id for role in member.roles}
            if role_ids:
                await self._sync_member_to_open_tickets(member, role_ids)
        except Exception:
            log.exception("Member-Sync (Beitritt) fehlgeschlagen")

    async def _sync_member_to_open_tickets(self, member: discord.Member, gained_role_ids: set):
        """Fügt ein Mitglied zu allen offenen Ticket-Threads hinzu, auf die es über
        seine Rollen Zugriff haben sollte.

        Textkanal-Tickets brauchen nichts – dort steuert die Rollen-Overwrite
        die Sichtbarkeit bereits automatisch.
        """
        guild = member.guild
        conf = await self.config.guild(guild).all()
        admin_role_id = conf.get("admin_role_id")
        categories = conf.get("categories", {})
        for ticket in conf.get("active_tickets", []):
            channel_id = ticket.get("channel_id")
            if not channel_id:
                continue
            relevant = {
                ticket.get("staff_role_id"),
                ticket.get("high_team_role_id"),
                admin_role_id,
            }
            if not relevant & gained_role_ids:
                # Fallback: Rollen aus der Kategorie (ältere Tickets ohne Snapshot)
                cat_data = categories.get(ticket.get("cat_id")) or {}
                relevant = {
                    cat_data.get("staff_role_id"),
                    cat_data.get("high_team_role_id"),
                    admin_role_id,
                }
                if not relevant & gained_role_ids:
                    continue
            channel = guild.get_channel_or_thread(channel_id)
            if not isinstance(channel, discord.Thread):
                continue
            try:
                # Vorab pruefen, ob das Mitglied bereits im Thread ist –
                # spart einen Rate-Limit-Treffer und vermeidet stumme Fehler.
                already = False
                try:
                    existing = await channel.fetch_members()
                    for tm in existing:
                        mid = getattr(tm, "id", None) or getattr(tm, "user_id", None)
                        if mid is not None and int(mid) == member.id:
                            already = True
                            break
                except discord.HTTPException:
                    pass
                if not already:
                    await channel.add_user(member)
            except discord.HTTPException as exc:
                log.debug(
                    "Konnte Mitglied %s nicht zu Thread #%s hinzufuegen: %s",
                    member.id,
                    getattr(channel, "name", channel.id),
                    exc,
                )

    async def _cleanup_deleted_ticket_channel(self, channel):
        try:
            guild = getattr(channel, "guild", None)
            if guild is None:
                return
            tickets = await self.config.guild(guild).active_tickets()
            ticket = self._find_ticket(tickets, channel.id)
            if ticket is None:
                return
            await self._remove_ticket(guild, channel.id)
            await self.send_log(
                guild,
                "🗑️ Ticket-Kanal gelöscht",
                discord.Color.dark_orange(),
                [
                    ("Ticket", _ticket_label(ticket)),
                    ("Hinweis", "Der Kanal wurde manuell gelöscht. Kein Transkript/Statistik erfasst."),
                ],
            )
            await self.update_panels(guild)
        except Exception:
            log.exception("Aufräumen nach Kanal-Löschung fehlgeschlagen")

    # -- Verwaltungsbefehle -------------------------------------------------------

    @commands.group(name="ticket", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def ticket_cmd(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @ticket_cmd.command(name="help")
    async def ticket_help(self, ctx: commands.Context):
        embed = discord.Embed(title="🎫 Ticket-System – Hilfe", color=discord.Color.blurple())
        embed.add_field(
            name="🛠️ Einrichtung",
            value=(
                "`[p]ticket setup` – Interaktives Basis-Setup\n"
                "`[p]ticket set` – Geführter Einrichtungs-Wizard\n"
                "`[p]ticket addcat` – Kategorie hinzufügen\n"
                "`[p]ticket managecats` – Kategorien bearbeiten/löschen\n"
                "`[p]ticket listcat` – Kategorien auflisten"
            ),
            inline=False,
        )
        embed.add_field(
            name="📣 Panel",
            value="`[p]ticket panel [#channel]` – Ticket-Panel posten",
            inline=False,
        )
        embed.add_field(
            name="⚙️ Verwaltung",
            value=(
                "`[p]ticket stats [Kategorie]` – Statistiken\n"
                "`[p]ticket history [@user]` – Ticket-Verlauf\n"
                "`[p]ticket export` – CSV-Export\n"
                "`[p]ticket showsettings` – Aktuelle Einstellungen\n"
                "`[p]ticket setlog #channel` / `[p]ticket setadminrole @rolle`\n"
                "`[p]ticket syncroles` – Rollen in Threads synchronisieren"
            ),
            inline=False,
        )
        embed.add_field(
            name="🚫 Sperrungen",
            value=(
                "`[p]ticket blacklist @user [Grund]`\n"
                "`[p]ticket unblacklist @user`\n"
                "`[p]ticket listblacklist`"
            ),
            inline=False,
        )
        embed.add_field(
            name="🎫 Innerhalb eines Tickets",
            value=(
                "`[p]tadd @user` – Nutzer hinzufügen\n"
                "`[p]tremove @user` – Nutzer entfernen\n"
                "`[p]trename Name` – Ticket umbenennen\n"
                "`[p]tclose [Grund]` – Ticket schließen\n"
                "`[p]ticket forceclose` – Sofort schließen (ohne Bewertung)"
            ),
            inline=False,
        )
        embed.add_field(
            name="🧹 Sonstiges",
            value="`[p]ticket reset confirm` – ALLE Daten dieses Servers zurücksetzen",
            inline=False,
        )
        await ctx.send(embed=embed)

    @ticket_cmd.command(name="setup")
    async def ticket_setup(self, ctx: commands.Context):
        """Interaktives Basis-Setup per Buttons/Dropdowns."""
        try:
            conf = await self.config.guild(ctx.guild).all()
            view = BaseSetupView(self, ctx, conf)
        except Exception:
            log.exception("Setup-View konnte nicht erstellt werden")
            return await ctx.send("❌ Setup konnte nicht gestartet werden.")
        embed = discord.Embed(
            title="🛠️ Basis-Setup",
            description=(
                "Passe die Einstellungen an und klicke anschließend auf **Setup abschließen**.\n"
                "Die aktuelle Konfiguration ist bereits vorausgefüllt."
            ),
            color=discord.Color.blurple(),
        )
        message = await ctx.send(embed=embed, view=view)
        view.message = message

    @ticket_cmd.command(name="set", aliases=["wizard"])
    async def ticket_set_wizard(self, ctx: commands.Context):
        """Startet den geführten Text-Wizard für die Einrichtung."""
        guild = ctx.guild
        author = ctx.author
        timeout = 120
        skip_words = ("", "skip", "keine", "keiner", "none", "-", "x", "überspringen")
        cancel_words = ("abbrechen", "abort", "cancel", "stop")

        def check(m: discord.Message) -> bool:
            return m.author == author and m.channel == ctx.channel

        async def ask(question: str):
            await ctx.send(question)
            try:
                reply = await self.bot.wait_for("message", check=check, timeout=timeout)
            except asyncio.TimeoutError:
                await ctx.send("⏰ Zeit abgelaufen. Bitte starte den Wizard erneut (`[p]ticket set`).")
                return None
            if reply.content.strip().lower() in cancel_words:
                await ctx.send("🛑 Wizard abgebrochen.")
                return None
            return reply.content

        async def ask_yes_no(question: str):
            while True:
                answer = await ask(question + " (ja/nein)")
                if answer is None:
                    return None
                lowered = answer.strip().lower()
                if lowered in ("ja", "j", "yes", "y", "true", "1"):
                    return True
                if lowered in ("nein", "n", "no", "false", "0"):
                    return False
                await ctx.send("❌ Bitte antworte mit 'ja' oder 'nein'.")

        # Log-Channel
        log_channel = None
        while log_channel is None:
            answer = await ask("Bitte gib den **Log-Channel** an (ID oder #Erwähnung):")
            if answer is None:
                return
            try:
                log_channel = await commands.TextChannelConverter().convert(ctx, answer)
            except commands.BadArgument:
                await ctx.send("❌ Kanal nicht gefunden. Versuche es erneut.")

        # Admin-Rolle (optional)
        admin_role = None
        while True:
            answer = await ask(
                "Bitte gib die **Admin-Rolle** an, die ALLE Tickets sehen und steuern darf "
                "(z. B. Inhaber-Rolle; ID/Name – oder 'skip' für keine).\n"
                "ℹ️ Support- und High-Team-Rollen werden pro Kategorie konfiguriert und "
                "sehen ihre Kategorien automatisch immer:"
            )
            if answer is None:
                return
            if answer.strip().lower() in skip_words:
                break
            try:
                admin_role = await commands.RoleConverter().convert(ctx, answer)
                break
            except commands.BadArgument:
                await ctx.send("❌ Rolle nicht gefunden. Versuche es erneut (oder 'skip').")

        # DM-Benachrichtigungen
        dm_on = await ask_yes_no("Sollen Nutzer **DM-Benachrichtigungen** erhalten?")
        if dm_on is None:
            return

        # Auto-Close
        autoclose = None
        while autoclose is None:
            answer = await ask(
                "Nach wie vielen Stunden **Inaktivität** soll ein Ticket automatisch "
                "geschlossen werden? (0 = aus)"
            )
            if answer is None:
                return
            try:
                autoclose = int(answer.strip())
                if autoclose < 0 or autoclose > 500:
                    raise ValueError
            except ValueError:
                await ctx.send("❌ Bitte eine Zahl zwischen 0 und 500 eingeben.")
                autoclose = None

        # Cooldown
        cooldown = None
        while cooldown is None:
            answer = await ask(
                "Wie lange soll der **Cooldown** zwischen zwei Tickets eines Nutzers sein? "
                "(Minuten, 0 = aus)"
            )
            if answer is None:
                return
            try:
                cooldown = int(answer.strip())
                if cooldown < 0 or cooldown > 10080:
                    raise ValueError
            except ValueError:
                await ctx.send("❌ Bitte eine Zahl zwischen 0 und 10080 eingeben.")
                cooldown = None

        # Max Tickets pro User
        max_tickets = None
        while max_tickets is None:
            answer = await ask(
                "Wie viele **offene Tickets pro Nutzer** sind gleichzeitig erlaubt? (1-10)"
            )
            if answer is None:
                return
            try:
                max_tickets = int(answer.strip())
                if max_tickets < 1 or max_tickets > 10:
                    raise ValueError
            except ValueError:
                await ctx.send("❌ Bitte eine Zahl zwischen 1 und 10 eingeben.")
                max_tickets = None

        # Threads löschen oder archivieren
        delete_threads = await ask_yes_no(
            "Sollen **Threads beim Schließen gelöscht** werden? (ja = löschen, nein = archivieren)"
        )
        if delete_threads is None:
            return

        # Auto-Eskalation
        auto_esc = None
        while auto_esc is None:
            answer = await ask(
                "Nach wie vielen Stunden soll ein Ticket mit Status 'Wartet auf Team' "
                "**automatisch eskaliert** werden? (0 = aus)"
            )
            if answer is None:
                return
            try:
                auto_esc = int(answer.strip())
                if auto_esc < 0 or auto_esc > 500:
                    raise ValueError
            except ValueError:
                await ctx.send("❌ Bitte eine Zahl zwischen 0 und 500 eingeben.")
                auto_esc = None

        # Kategorien hinzufügen
        new_categories = {}
        while True:
            answer = await ask_yes_no("Möchtest du jetzt eine **Support-Kategorie** hinzufügen?")
            if answer is None:
                return
            if not answer:
                break

            cat_name = None
            while cat_name is None:
                cat_name = await ask("**Name der Kategorie** (z. B. Allgemeiner Support):")
                if cat_name is None:
                    return
                cat_name = cat_name.strip()
                if not cat_name:
                    cat_name = None

            cat_desc = await ask("**Beschreibung** (optional – 'skip' zum Überspringen):")
            if cat_desc is None:
                return
            cat_desc = None if cat_desc.strip().lower() in skip_words else cat_desc.strip()

            cat_emoji = await ask("**Emoji** (optional – 'skip' für Standard 🎫):")
            if cat_emoji is None:
                return
            cat_emoji = "🎫" if cat_emoji.strip().lower() in skip_words else _sanitize_emoji(cat_emoji.strip())

            cat_abbr = None
            while cat_abbr is None:
                cat_abbr = await ask("**Abkürzung** für Kanalnamen (z. B. SUP):")
                if cat_abbr is None:
                    return
                cat_abbr = cat_abbr.strip().upper()
                if not cat_abbr:
                    cat_abbr = None
                elif len(cat_abbr) > 10:
                    await ctx.send("❌ Abkürzung max. 10 Zeichen.")
                    cat_abbr = None

            typ = None
            while typ is None:
                answer = await ask(
                    "Sollen Tickets als **Textkanal** in einer Kategorie oder als **Thread** "
                    "erstellt werden? (channel/thread)"
                )
                if answer is None:
                    return
                lowered = answer.strip().lower()
                if lowered in ("channel", "k", "kanal", "c"):
                    typ = "channel"
                elif lowered in ("thread", "t", "faden"):
                    typ = "thread"
                else:
                    await ctx.send("❌ Bitte 'channel' oder 'thread' eingeben.")

            discord_cat_id = None
            thread_parent_id = None
            if typ == "channel":
                while discord_cat_id is None:
                    answer = await ask("Bitte gib die **Discord-Kategorie** an (ID oder Name):")
                    if answer is None:
                        return
                    try:
                        category = await commands.CategoryChannelConverter().convert(ctx, answer)
                        discord_cat_id = category.id
                    except commands.BadArgument:
                        await ctx.send("❌ Kategorie nicht gefunden.")
            else:
                while thread_parent_id is None:
                    answer = await ask(
                        "Bitte gib den **Textkanal** an, in dem Threads erstellt werden sollen "
                        "(ID oder #Erwähnung):"
                    )
                    if answer is None:
                        return
                    try:
                        parent = await commands.TextChannelConverter().convert(ctx, answer)
                        thread_parent_id = parent.id
                    except commands.BadArgument:
                        await ctx.send("❌ Textkanal nicht gefunden.")

            staff_role_id = None
            while staff_role_id is None:
                answer = await ask("Bitte gib die **Support-Rolle** an (ID oder Name):")
                if answer is None:
                    return
                try:
                    role = await commands.RoleConverter().convert(ctx, answer)
                    staff_role_id = role.id
                except commands.BadArgument:
                    await ctx.send("❌ Rolle nicht gefunden.")

            high_role_id = None
            answer = await ask("**High-Team-Rolle** (optional – 'skip' zum Überspringen):")
            if answer is None:
                return
            if answer.strip().lower() not in skip_words:
                try:
                    high_role = await commands.RoleConverter().convert(ctx, answer)
                    high_role_id = high_role.id
                except commands.BadArgument:
                    await ctx.send("❌ Rolle nicht gefunden. High-Team wird übersprungen.")

            cat_max = None
            while cat_max is None:
                answer = await ask(
                    "Maximale **aktive Tickets** in dieser Kategorie gleichzeitig? (0 = unbegrenzt)"
                )
                if answer is None:
                    return
                try:
                    cat_max = int(answer.strip())
                    if cat_max < 0 or cat_max > 100:
                        raise ValueError
                except ValueError:
                    await ctx.send("❌ Bitte eine Zahl zwischen 0 und 100.")
                    cat_max = None

            cat_id = str(uuid.uuid4())[:8]
            new_categories[cat_id] = {
                "name": cat_name,
                "description": cat_desc,
                "emoji": cat_emoji,
                "abbr": cat_abbr,
                "discord_category_id": discord_cat_id,
                "thread_parent_id": thread_parent_id,
                "staff_role_id": staff_role_id,
                "high_team_role_id": high_role_id,
                "max_tickets": cat_max,
            }
            await ctx.send(f"✅ Kategorie **{cat_name}** hinzugefügt.")

        # Panel posten?
        panel_channel = None
        answer = await ask_yes_no("Möchtest du das **Ticket-Panel** jetzt in einem Kanal posten?")
        if answer is None:
            return
        if answer:
            while panel_channel is None:
                answer = await ask(
                    "Bitte gib den **Textkanal** für das Panel an (ID oder #Erwähnung):"
                )
                if answer is None:
                    return
                try:
                    panel_channel = await commands.TextChannelConverter().convert(ctx, answer)
                except commands.BadArgument:
                    await ctx.send("❌ Kanal nicht gefunden.")

        # Speichern
        guild_conf = self.config.guild(guild)
        await guild_conf.log_channel_id.set(log_channel.id)
        await guild_conf.admin_role_id.set(admin_role.id if admin_role else None)
        await guild_conf.dm_notifications.set(dm_on)
        await guild_conf.autoclose_hours.set(autoclose)
        await guild_conf.cooldown_minutes.set(cooldown)
        await guild_conf.max_tickets_per_user.set(max_tickets)
        await guild_conf.delete_threads_after_close.set(delete_threads)
        await guild_conf.auto_escalate_hours.set(auto_esc)
        if new_categories:
            existing = await guild_conf.categories()
            existing.update(new_categories)
            await guild_conf.categories.set(existing)

        panel_info = "Nicht gepostet"
        if panel_channel:
            panel_message = await self.create_panel(panel_channel)
            panel_info = panel_channel.mention if panel_message else "Fehlgeschlagen (keine Kategorien?)"

        await ctx.send(
            "✅ **Setup abgeschlossen!**\n"
            f"Log-Channel: {log_channel.mention}\n"
            f"Admin-Rolle: {admin_role.mention if admin_role else 'Nicht gesetzt'}\n"
            f"Neue Kategorien: {len(new_categories)} (bestehende bleiben erhalten)\n"
            f"Panel: {panel_info}"
        )

    @ticket_cmd.command(name="addcat")
    async def ticket_addcat(self, ctx: commands.Context):
        """Fügt über eine interaktive Ansicht eine neue Kategorie hinzu."""
        try:
            view = CategorySetupView(self, ctx)
        except Exception:
            log.exception("Kategorie-View konnte nicht erstellt werden")
            return await ctx.send("❌ Kategorie-Setup konnte nicht gestartet werden.")
        embed = discord.Embed(
            title="🏷️ Kategorie-Setup",
            description=(
                "Konfiguriere die neue Kategorie:\n"
                "1. **Texte anpassen** (Name, Beschreibung, Abkürzung, Emoji)\n"
                "2. **Discord-Kategorie** *oder* **Thread-Channel** wählen\n"
                "3. **Support-Rolle** (Pflicht) und optional High-Team-Rolle\n"
                "4. **Speichern** drücken\n\n"
                "ℹ️ Support- und High-Team-Rollen sehen alle Tickets ihrer Kategorie – "
                "auch Mitglieder, die die Rolle später erhalten. Bei Thread-Tickets "
                "muss der Thread-Channel für die Support-Rolle sichtbar sein."
            ),
            color=discord.Color.blurple(),
        )
        message = await ctx.send(embed=embed, view=view)
        view.message = message

    @ticket_cmd.command(name="listcat")
    async def ticket_listcat(self, ctx: commands.Context):
        """Listet alle Ticket-Kategorien auf."""
        categories = await self.config.guild(ctx.guild).categories()
        if not categories:
            return await ctx.send("❌ Es wurden noch keine Kategorien erstellt (`[p]ticket addcat`).")
        active_tickets = await self.config.guild(ctx.guild).active_tickets()
        lines = ["**📦 Ticket-Kategorien:**"]
        for cat_id, data in categories.items():
            emoji = _sanitize_emoji(data.get("emoji"), "🎫")
            typ = "Thread" if data.get("thread_parent_id") else "Kanal"
            if not data.get("thread_parent_id") and not data.get("discord_category_id"):
                typ = "⚠️ Kein Ziel konfiguriert"
            open_count = sum(1 for t in active_tickets if t.get("cat_id") == cat_id)
            staff = ctx.guild.get_role(data.get("staff_role_id")) if data.get("staff_role_id") else None
            high = ctx.guild.get_role(data.get("high_team_role_id")) if data.get("high_team_role_id") else None
            lines.append(
                f"{emoji} **{data.get('name', '?')}** (`{cat_id}`)\n"
                f"　Typ: {typ} | Offen: {open_count} | Max: {data.get('max_tickets', 10) or '∞'} | "
                f"Abkürzung: `{data.get('abbr', '?')}`\n"
                f"　Support: {staff.mention if staff else '⚠️ nicht gesetzt'} | "
                f"High-Team: {high.mention if high else '—'}"
            )
        await ctx.send("\n".join(lines)[:2000])

    @ticket_cmd.command(name="managecats")
    async def ticket_managecats(self, ctx: commands.Context):
        """Kategorien über ein Menü bearbeiten oder löschen."""
        categories = await self.config.guild(ctx.guild).categories()
        if not categories:
            return await ctx.send("❌ Es gibt keine Kategorien zu verwalten.")
        options = [
            discord.SelectOption(label=str(c.get("name", "?"))[:100], value=cat_id)
            for cat_id, c in categories.items()
        ][:25]
        view = discord.ui.View(timeout=300)
        select = discord.ui.Select(placeholder="Kategorie wählen", options=options)

        async def select_cb(interaction: discord.Interaction):
            cat_id = select.values[0]
            cat_data = categories.get(cat_id)
            if cat_data is None:
                return await interaction.response.edit_message(
                    content="❌ Kategorie nicht mehr vorhanden.", view=None
                )
            edit_view = discord.ui.View(timeout=300)
            btn_edit = discord.ui.Button(label="Bearbeiten", style=discord.ButtonStyle.primary)
            btn_del = discord.ui.Button(label="Löschen", style=discord.ButtonStyle.danger)
            btn_back = discord.ui.Button(label="Abbrechen", style=discord.ButtonStyle.secondary)

            async def edit_cb(interaction2: discord.Interaction):
                setup_view = CategorySetupView(self, ctx, cat_id=cat_id, cat_data=cat_data)
                await interaction2.response.edit_message(
                    embed=discord.Embed(
                        title="✏️ Kategorie bearbeiten",
                        description="Passe die Werte an und klicke auf **Speichern**.",
                        color=discord.Color.blurple(),
                    ),
                    view=setup_view,
                )
                setup_view.message = interaction2.message

            async def del_cb(interaction2: discord.Interaction):
                current = await self.config.guild(ctx.guild).categories()
                if cat_id in current:
                    del current[cat_id]
                    await self.config.guild(ctx.guild).categories.set(current)
                    await self.update_panels(ctx.guild)
                    await interaction2.response.edit_message(
                        content=(
                            f"✅ Kategorie **{cat_data.get('name')}** gelöscht. "
                            "Laufende Tickets dieser Kategorie bleiben funktionsfähig."
                        ),
                        view=None,
                    )
                else:
                    await interaction2.response.edit_message(
                        content="❌ Kategorie nicht gefunden.", view=None
                    )

            async def back_cb(interaction2: discord.Interaction):
                await interaction2.response.edit_message(content="Abgebrochen.", view=None)

            btn_edit.callback = edit_cb
            btn_del.callback = del_cb
            btn_back.callback = back_cb
            edit_view.add_item(btn_edit)
            edit_view.add_item(btn_del)
            edit_view.add_item(btn_back)
            await interaction.response.edit_message(
                content=f"Kategorie **{cat_data.get('name')}** ausgewählt.", view=edit_view
            )

        select.callback = select_cb
        view.add_item(select)
        await ctx.send("Kategorie auswählen:", view=view)

    @ticket_cmd.command(name="panel")
    async def ticket_panel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Postet das Ticket-Panel im aktuellen oder angegebenen Kanal."""
        channel = channel or ctx.channel
        categories = await self.config.guild(ctx.guild).categories()
        if not categories:
            return await ctx.send("❌ Erstelle zuerst mindestens eine Kategorie (`[p]ticket addcat`).")
        perms = channel.permissions_for(ctx.guild.me)
        if not (perms.send_messages and perms.embed_links):
            return await ctx.send(
                "❌ Ich brauche in dem Kanal die Rechte **Nachrichten senden** und **Links einbetten**."
            )
        message = await self.create_panel(channel)
        if message:
            await ctx.send(f"✅ Panel in {channel.mention} gepostet.")
        else:
            await ctx.send("❌ Panel konnte nicht gepostet werden.")

    @ticket_cmd.command(name="blacklist")
    async def ticket_blacklist(self, ctx: commands.Context, user: discord.User, *, reason: str = "Kein Grund"):
        """Sperrt einen Nutzer vom Ticket-System."""
        blacklist = await self.config.guild(ctx.guild).blacklist()
        if user.id in blacklist:
            return await ctx.send("❌ Dieser Nutzer ist bereits gesperrt.")
        blacklist.append(user.id)
        await self.config.guild(ctx.guild).blacklist.set(blacklist)
        await ctx.send(f"✅ {user.mention} wurde gesperrt. Grund: {reason}")
        await self.send_log(
            ctx.guild,
            "🚫 Nutzer gesperrt",
            discord.Color.dark_red(),
            [("Nutzer", user.mention), ("Grund", reason)],
        )

    @ticket_cmd.command(name="unblacklist")
    async def ticket_unblacklist(self, ctx: commands.Context, user: discord.User):
        """Entsperrt einen Nutzer."""
        blacklist = await self.config.guild(ctx.guild).blacklist()
        if user.id not in blacklist:
            return await ctx.send("❌ Dieser Nutzer ist nicht gesperrt.")
        blacklist.remove(user.id)
        await self.config.guild(ctx.guild).blacklist.set(blacklist)
        await ctx.send(f"✅ {user.mention} wurde entsperrt.")

    @ticket_cmd.command(name="listblacklist")
    async def ticket_listblacklist(self, ctx: commands.Context):
        """Zeigt alle gesperrten Nutzer."""
        blacklist = await self.config.guild(ctx.guild).blacklist()
        if not blacklist:
            return await ctx.send("✅ Keine Nutzer sind gesperrt.")
        lines = [f"<@{user_id}>" for user_id in blacklist[:50]]
        await ctx.send("**🚫 Gesperrte Nutzer:**\n" + "\n".join(lines))

    @ticket_cmd.command(name="forceclose")
    async def ticket_forceclose(self, ctx: commands.Context):
        """Schließt das Ticket im aktuellen Kanal sofort (ohne Bewertung)."""
        tickets = await self.config.guild(ctx.guild).active_tickets()
        ticket = self._find_ticket(tickets, ctx.channel.id)
        if not ticket:
            return await ctx.send("❌ Dieser Befehl funktioniert nur in einem aktiven Ticket.")
        await ctx.send("⚠️ Ticket wird sofort geschlossen...")
        await self.close_ticket(
            ctx.channel, "Manuell erzwungen (Force-Close)", ctx.author, force_delete=True
        )

    @ticket_cmd.command(name="stats")
    async def ticket_stats(self, ctx: commands.Context, *, category: str = None):
        """Zeigt Statistiken (gesamt oder pro Kategorie)."""
        conf = await self.config.guild(ctx.guild).all()
        stats = conf.get("stats", {})
        active_tickets = conf.get("active_tickets", [])
        total_created = conf.get("total_tickets_created", 0)
        category_stats = conf.get("category_stats", {})
        categories = conf.get("categories", {})

        if not stats and not active_tickets and not total_created and not category_stats:
            return await ctx.send("Es gibt noch keine Statistiken.")

        if category:
            cat_id = None
            for cid, cat_data in categories.items():
                matches = (
                    str(cat_data.get("name", "")).lower(),
                    str(cat_data.get("abbr", "")).lower(),
                    str(cid).lower(),
                )
                if category.strip().lower() in matches:
                    cat_id = cid
                    break
            if not cat_id:
                return await ctx.send(
                    "❌ Kategorie nicht gefunden. Nutze `[p]ticket listcat` für eine Übersicht."
                )
            cat_data = categories[cat_id]
            cs = category_stats.get(cat_id, {})
            closed = cs.get("closed", 0)
            duration = cs.get("total_duration_minutes", 0)
            count = cs.get("ticket_count", 0)
            embed = discord.Embed(
                title=f"📊 Statistik: {cat_data.get('name')}", color=discord.Color.gold()
            )
            embed.add_field(name="Erstellt", value=str(cs.get("created", 0)), inline=True)
            embed.add_field(name="Geschlossen", value=str(closed), inline=True)
            embed.add_field(
                name="Offen",
                value=str(sum(1 for t in active_tickets if t.get("cat_id") == cat_id)),
                inline=True,
            )
            embed.add_field(
                name="Ø Bearbeitungsdauer",
                value=_fmt_duration(duration / count) if count else "—",
                inline=True,
            )
            return await ctx.send(embed=embed)

        total_closed = sum(u.get("closed", 0) for u in stats.values())
        total_reaction = sum(u.get("total_reaction_minutes", 0) for u in stats.values())
        reaction_count = sum(u.get("reaction_count", 0) for u in stats.values())
        total_duration = sum(u.get("total_duration_minutes", 0) for u in stats.values())
        duration_count = sum(u.get("ticket_count", 0) for u in stats.values())
        star_total = 0
        star_count = 0
        for user_stats in stats.values():
            stars = user_stats.get("stars", [0, 0, 0, 0, 0])
            for index, count in enumerate(stars):
                star_total += (index + 1) * count
                star_count += count

        embed = discord.Embed(title="📊 Support-System Statistik", color=discord.Color.gold())
        embed.add_field(name="Erstellt", value=str(total_created), inline=True)
        embed.add_field(name="Geschlossen", value=str(total_closed), inline=True)
        embed.add_field(name="Offen", value=str(len(active_tickets)), inline=True)
        embed.add_field(
            name="Ø Erste Reaktion",
            value=f"{total_reaction / reaction_count:.1f} Min" if reaction_count else "—",
            inline=True,
        )
        embed.add_field(
            name="Ø Bearbeitungsdauer",
            value=_fmt_duration(total_duration / duration_count) if duration_count else "—",
            inline=True,
        )
        embed.add_field(
            name="Ø Bewertung",
            value=f"{star_total / star_count:.1f} ⭐" if star_count else "—",
            inline=True,
        )

        if stats:
            sorted_stats = sorted(stats.items(), key=lambda item: item[1].get("closed", 0), reverse=True)
            description = ""
            for user_id, user_stats in sorted_stats[:10]:
                member = ctx.guild.get_member(int(user_id))
                name = member.display_name if member else f"Unbekannt ({user_id})"
                stars = user_stats.get("stars", [0, 0, 0, 0, 0])
                s_total = sum((i + 1) * c for i, c in enumerate(stars))
                s_count = sum(stars)
                avg_stars = f"{s_total / s_count:.1f}⭐" if s_count else "—"
                description += (
                    f"**{name}**: {user_stats.get('closed', 0)} geschlossen | "
                    f"{user_stats.get('claimed', 0)} übernommen | Ø {avg_stars}\n"
                )
            embed.add_field(name="🏆 Top Support", value=description[:1024] or "—", inline=False)

        if conf.get("show_category_stats", True):
            use_charts = conf.get("use_emoji_charts", True)
            for cat_id, cat_data in list(categories.items())[:12]:
                cs = category_stats.get(cat_id, {})
                open_count = sum(1 for t in active_tickets if t.get("cat_id") == cat_id)
                closed = cs.get("closed", 0)
                created = cs.get("created", 0)
                emoji = _sanitize_emoji(cat_data.get("emoji"))
                if use_charts and created > 0:
                    bar = self._emoji_bar(closed, created)
                    value = f"`{bar}` {closed}/{created} geschlossen | {open_count} offen"
                else:
                    value = f"{closed}/{created} geschlossen | {open_count} offen"
                embed.add_field(name=f"{emoji} {cat_data.get('name', '?')}", value=value[:1024], inline=True)

        await ctx.send(embed=embed)

    @ticket_cmd.command(name="history")
    async def ticket_history(self, ctx: commands.Context, user: discord.User = None):
        """Zeigt den Ticket-Verlauf eines Nutzers."""
        user = user or ctx.author
        history = await self.config.guild(ctx.guild).ticket_history()
        user_tickets = [t for t in history if t.get("user_id") == user.id]
        if not user_tickets:
            return await ctx.send(f"Es gibt keine Ticket-Historie für {user.mention}.")
        categories = await self.config.guild(ctx.guild).categories()
        embed = discord.Embed(
            title=f"🗂️ Ticket-Verlauf: {user.display_name}", color=discord.Color.blue()
        )
        for ticket in user_tickets[-10:]:
            cat_data = categories.get(ticket.get("cat_id"), {}) or {}
            cat_name = cat_data.get("name", "Unbekannt")
            created = _parse_dt(ticket.get("created_at"))
            closed_raw = ticket.get("closed_at")
            closed = _parse_dt(closed_raw).strftime("%d.%m.%Y %H:%M") if closed_raw else "—"
            stars = ticket.get("stars") or 0
            reason = ticket.get("close_reason") or "Kein Grund"
            number = ticket.get("number")
            title = f"#{number:04d} – {cat_name} – {created.strftime('%d.%m.%Y')}" if number else (
                f"{cat_name} – {created.strftime('%d.%m.%Y')}"
            )
            embed.add_field(
                name=title[:256],
                value=(
                    f"Geschlossen: {closed}\n"
                    f"Bewertung: {'⭐' * stars if stars else 'Keine'}\n"
                    f"Grund: {str(reason)[:200]}"
                ),
                inline=False,
            )
        embed.set_footer(text=f"Insgesamt {len(user_tickets)} Ticket(s) – letzte 10 angezeigt")
        await ctx.send(embed=embed)

    @ticket_cmd.command(name="syncroles")
    async def ticket_syncroles(self, ctx: commands.Context):
        """Synchronisiert konfigurierte Rollen in alle bekannten Ticket-Threads."""
        guild = ctx.guild
        active = await self.config.guild(guild).active_tickets()
        history = await self.config.guild(guild).ticket_history()
        thread_ids = {t.get("channel_id") for t in active if t.get("channel_id")}
        thread_ids |= {h.get("channel_id") for h in history if h.get("channel_id")}
        synced = 0
        skipped = 0
        total_added = 0
        total_skipped = 0
        total_failed = 0
        status_message = await ctx.send("🔄 Synchronisiere Rollen in Ticket-Threads...")
        for channel_id in thread_ids:
            channel = guild.get_channel_or_thread(channel_id)
            if channel is None:
                # Archivierte Threads sind nach einem Neustart nicht im Cache
                try:
                    channel = await guild.fetch_channel(channel_id)
                except discord.HTTPException:
                    skipped += 1
                    continue
            if not isinstance(channel, discord.Thread):
                skipped += 1
                continue
            try:
                was_archived = channel.archived
                if was_archived:
                    await self._set_thread_archived(channel, False)
                result = await self._sync_roles_to_thread(channel, guild)
                if was_archived:
                    await self._set_thread_archived(channel, True)
                synced += 1
                if isinstance(result, dict):
                    total_added += int(result.get("added", 0))
                    total_skipped += int(result.get("skipped", 0))
                    total_failed += int(result.get("failed", 0)) + int(result.get("uncached", 0))
            except Exception:
                log.exception("Sync fehlgeschlagen fuer Kanal %s", channel_id)
                skipped += 1
        await status_message.edit(
            content=(
                f"✅ **{synced}** Thread(s) synchronisiert – "
                f"➕ {total_added} Mitglieder neu hinzugefuegt, "
                f"⏭️ {total_skipped} bereits vorhanden, "
                f"❌ {total_failed} fehlgeschlagen. "
                f"{skipped} Thread(s) uebersprungen."
            )
        )

    @ticket_cmd.command(name="export")
    async def ticket_export(self, ctx: commands.Context):
        """Exportiert Statistiken als CSV-Datei."""
        conf = await self.config.guild(ctx.guild).all()
        stats = conf.get("stats", {})
        category_stats = conf.get("category_stats", {})
        categories = conf.get("categories", {})
        active_tickets = conf.get("active_tickets", [])
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["User", "Claimed", "Closed", "Ø Dauer (Min)", "Sterne (Ø)"])
        for user_id, user_stats in stats.items():
            try:
                member = ctx.guild.get_member(int(user_id))
                name = member.display_name if member else str(user_id)
            except ValueError:
                name = str(user_id)
            count = user_stats.get("ticket_count", 0)
            duration = user_stats.get("total_duration_minutes", 0)
            avg_duration = round(duration / count, 1) if count else 0
            stars = user_stats.get("stars", [0, 0, 0, 0, 0])
            star_total = sum((i + 1) * c for i, c in enumerate(stars))
            star_count = sum(stars)
            avg_stars = round(star_total / star_count, 2) if star_count else 0
            writer.writerow(
                [name, user_stats.get("claimed", 0), user_stats.get("closed", 0), avg_duration, avg_stars]
            )
        writer.writerow([])
        writer.writerow(["Kategorie", "Erstellt", "Geschlossen", "Offen", "Ø Dauer (Min)"])
        for cat_id, cs in category_stats.items():
            cat_name = categories.get(cat_id, {}).get("name", "Unbekannt")
            open_count = sum(1 for t in active_tickets if t.get("cat_id") == cat_id)
            count = cs.get("ticket_count", 0)
            duration = cs.get("total_duration_minutes", 0)
            avg_duration = round(duration / count, 1) if count else 0
            writer.writerow(
                [cat_name, cs.get("created", 0), cs.get("closed", 0), open_count, avg_duration]
            )
        file = discord.File(io.StringIO(buffer.getvalue()), filename="ticket_stats.csv")
        await ctx.send(file=file)

    @ticket_cmd.command(name="showsettings")
    async def ticket_showsettings(self, ctx: commands.Context):
        """Zeigt die aktuelle Konfiguration."""
        conf = await self.config.guild(ctx.guild).all()
        log_channel = ctx.guild.get_channel(conf.get("log_channel_id"))
        admin_role = ctx.guild.get_role(conf.get("admin_role_id")) if conf.get("admin_role_id") else None

        def on_off(value: bool) -> str:
            return "✅ AN" if value else "❌ AUS"

        embed = discord.Embed(title="⚙️ Ticket-System – Einstellungen", color=discord.Color.blurple())
        embed.add_field(name="Log-Channel", value=log_channel.mention if log_channel else "⚠️ Nicht gesetzt", inline=True)
        embed.add_field(name="Admin-Rolle", value=admin_role.mention if admin_role else "—", inline=True)
        embed.add_field(name="DM-Benachrichtigungen", value=on_off(conf.get("dm_notifications", True)), inline=True)
        embed.add_field(name="Auto-Close", value=f"{conf.get('autoclose_hours', 48)} Std", inline=True)
        embed.add_field(name="Cooldown", value=f"{conf.get('cooldown_minutes', 0)} Min", inline=True)
        embed.add_field(name="Max Tickets/User", value=str(conf.get("max_tickets_per_user", 1)), inline=True)
        embed.add_field(name="Threads löschen", value=on_off(conf.get("delete_threads_after_close", False)), inline=True)
        embed.add_field(name="Auto-Eskalation", value=f"{conf.get('auto_escalate_hours', 0)} Std (0 = aus)", inline=True)
        embed.add_field(name="Kategorie-Statistiken", value=on_off(conf.get("show_category_stats", True)), inline=True)
        embed.add_field(name="Emoji-Balken", value=on_off(conf.get("use_emoji_charts", True)), inline=True)
        embed.add_field(
            name="Bestand",
            value=(
                f"Kategorien: {len(conf.get('categories', {}))}\n"
                f"Panels: {len(conf.get('panels', []))}\n"
                f"Offene Tickets: {len(conf.get('active_tickets', []))}\n"
                f"Gesamt erstellt: {conf.get('total_tickets_created', 0)}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Sichtbarkeit",
            value=(
                "Admin-Rolle sieht **alle** Tickets (übergreifend).\n"
                "Support-/High-Team-Rollen sehen die Tickets **ihrer Kategorien** – "
                "inkl. später hinzugefügter Rollenmitglieder."
            ),
            inline=False,
        )
        embed.set_footer(text=f"SupportCog V{__version__}")
        await ctx.send(embed=embed)

    @ticket_cmd.command(name="setlog")
    async def ticket_setlog(self, ctx: commands.Context, channel: discord.TextChannel):
        """Setzt den Log-Channel."""
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
        await ctx.send(f"✅ Log-Channel gesetzt auf {channel.mention}.")

    @ticket_cmd.command(name="setadminrole")
    async def ticket_setadminrole(self, ctx: commands.Context, role: discord.Role = None):
        """Setzt die Admin-Rolle (oder entfernt sie ohne Angabe)."""
        await self.config.guild(ctx.guild).admin_role_id.set(role.id if role else None)
        await ctx.send(
            f"✅ Admin-Rolle gesetzt auf {role.mention}." if role else "✅ Admin-Rolle entfernt."
        )

    @ticket_cmd.command(name="reset")
    async def ticket_reset(self, ctx: commands.Context, confirmation: str = None):
        """Setzt ALLE Ticket-Daten dieses Servers zurück (mit Bestätigung)."""
        if confirmation != "confirm":
            return await ctx.send(
                "⚠️ **Achtung:** Dies löscht ALLE Einstellungen, Kategorien, Statistiken und "
                "den Verlauf dieses Servers (bestehende Ticket-Kanäle bleiben bestehen).\n"
                "Zum Bestätigen: `[p]ticket reset confirm`"
            )
        await self.config.guild(ctx.guild).clear()
        self._active_channel_cache[ctx.guild.id] = set()
        await ctx.send("✅ Konfiguration zurückgesetzt. Bitte richte das System neu ein (`[p]ticket setup`).")

    # -- Support-Befehle (innerhalb von Tickets) -----------------------------------

    @commands.command(name="tadd")
    @commands.guild_only()
    async def tadd(self, ctx: commands.Context, user: discord.Member):
        """Fügt einen Nutzer zum aktuellen Ticket hinzu."""
        tickets = await self.config.guild(ctx.guild).active_tickets()
        ticket = self._find_ticket(tickets, ctx.channel.id)
        if not ticket:
            return await ctx.send("❌ Dieser Befehl funktioniert nur in einem aktiven Ticket.")
        if not await self.is_support(ctx.author, ctx.guild, ticket):
            return await ctx.send("❌ Keine Berechtigung.", delete_after=10)
        try:
            if isinstance(ctx.channel, discord.Thread):
                await ctx.channel.add_user(user)
            else:
                await ctx.channel.set_permissions(
                    user, view_channel=True, send_messages=True, read_message_history=True
                )
            await ctx.send(f"✅ {user.mention} wurde zum Ticket hinzugefügt.")
        except discord.HTTPException as error:
            await ctx.send(f"❌ Fehler: {error}")

    @commands.command(name="tremove")
    @commands.guild_only()
    async def tremove(self, ctx: commands.Context, user: discord.Member):
        """Entfernt einen Nutzer aus dem aktuellen Ticket."""
        tickets = await self.config.guild(ctx.guild).active_tickets()
        ticket = self._find_ticket(tickets, ctx.channel.id)
        if not ticket:
            return await ctx.send("❌ Dieser Befehl funktioniert nur in einem aktiven Ticket.")
        if ticket.get("user_id") == user.id:
            return await ctx.send("❌ Du kannst den Ersteller nicht entfernen.")
        if not await self.is_support(ctx.author, ctx.guild, ticket):
            return await ctx.send("❌ Keine Berechtigung.", delete_after=10)
        try:
            if isinstance(ctx.channel, discord.Thread):
                await ctx.channel.remove_user(user)
            else:
                await ctx.channel.set_permissions(user, overwrite=None)
            await ctx.send(f"✅ {user.mention} wurde aus dem Ticket entfernt.")
        except discord.HTTPException as error:
            await ctx.send(f"❌ Fehler: {error}")

    @commands.command(name="trename")
    @commands.guild_only()
    async def trename(self, ctx: commands.Context, *, new_name: str):
        """Benennt das aktuelle Ticket um."""
        tickets = await self.config.guild(ctx.guild).active_tickets()
        ticket = self._find_ticket(tickets, ctx.channel.id)
        if not ticket:
            return await ctx.send("❌ Dieser Befehl funktioniert nur in einem aktiven Ticket.")
        if not await self.is_support(ctx.author, ctx.guild, ticket):
            return await ctx.send("❌ Keine Berechtigung.", delete_after=10)
        cleaned = new_name.strip()[:100]
        try:
            await ctx.channel.edit(name=cleaned)
            await ctx.send(f"✅ Ticket umbenannt in `{cleaned}`.")
        except discord.HTTPException as error:
            await ctx.send(f"❌ Fehler: {error}")

    @commands.command(name="tclose")
    @commands.guild_only()
    async def tclose(self, ctx: commands.Context, *, reason: str = "Kein Grund angegeben"):
        """Schließt das aktuelle Ticket (Support)."""
        tickets = await self.config.guild(ctx.guild).active_tickets()
        ticket = self._find_ticket(tickets, ctx.channel.id)
        if not ticket:
            return await ctx.send("❌ Dieser Befehl funktioniert nur in einem aktiven Ticket.")
        if not await self.is_support(ctx.author, ctx.guild, ticket):
            return await ctx.send("❌ Keine Berechtigung.", delete_after=10)
        await ctx.send("🔒 Ticket wird geschlossen...")
        await self.close_ticket(ctx.channel, reason, ctx.author)

    # -- Kern-Logik: Berechtigungen & Panels ----------------------------------------

    async def is_support(self, member, guild: discord.Guild, ticket_data: dict) -> bool:
        """Prüft, ob ein Mitglied Support-Rechte für das Ticket hat.

        Gilt für: Server-Manager, die Admin-Rolle (z. B. Inhaber – sieht ALLE
        Tickets) sowie die Support-/High-Team-Rolle der Ticket-Kategorie.
        """
        if not isinstance(member, discord.Member):
            return False
        try:
            if member.guild_permissions.manage_guild:
                return True
        except AttributeError:
            return False
        conf = await self.config.guild(guild).all()
        role_ids = {role.id for role in member.roles}
        if conf.get("admin_role_id") in role_ids:
            return True
        staff_role_id = ticket_data.get("staff_role_id")
        high_role_id = ticket_data.get("high_team_role_id")
        if staff_role_id is None and high_role_id is None:
            cat_data = conf.get("categories", {}).get(ticket_data.get("cat_id"), {}) or {}
            staff_role_id = cat_data.get("staff_role_id")
            high_role_id = cat_data.get("high_team_role_id")
        return staff_role_id in role_ids or high_role_id in role_ids

    def _build_panel_options(self, categories: dict, active_tickets: list) -> list:
        options = []
        for cat_id, cat_data in categories.items():
            active_count = sum(1 for t in active_tickets if t.get("cat_id") == cat_id)
            max_tickets = cat_data.get("max_tickets", 10)
            name = str(cat_data.get("name", "Kategorie"))[:60]
            if max_tickets and max_tickets > 0:
                percent = min(100, int((active_count / max_tickets) * 100))
                label = f"{name} ({active_count}/{max_tickets})"[:100]
                description = (str(cat_data.get("description") or "") + f" – {percent}% ausgelastet").strip(" –")[:100]
            else:
                label = name[:100]
                description = str(cat_data.get("description") or "")[:100] or None
            options.append(
                discord.SelectOption(
                    label=label,
                    value=cat_id,
                    description=description,
                    emoji=_sanitize_emoji(cat_data.get("emoji")),
                )
            )
        return options[:25]

    def _make_panel_view(self, categories: dict, active_tickets: list) -> TicketPanelView:
        view = TicketPanelView(self)
        options = self._build_panel_options(categories, active_tickets)
        for child in view.children:
            if isinstance(child, discord.ui.Select):
                if options:
                    child.options = options
                    child.disabled = False
                else:
                    child.options = [discord.SelectOption(label="Keine Kategorien verfügbar", value="none")]
                    child.disabled = True
        return view

    async def _build_panel_embed(
        self,
        guild: discord.Guild,
        categories: dict | None = None,
        active_tickets: list | None = None,
    ) -> discord.Embed:
        conf = await self.config.guild(guild).all()
        if categories is None:
            categories = conf.get("categories", {})
        if active_tickets is None:
            active_tickets = conf.get("active_tickets", [])

        embed = discord.Embed(
            title="🎫 Support Ticket System",
            description=(
                "Brauchst du Hilfe? Wähle unten im Dropdown-Menü die passende Kategorie aus.\n"
                "Die Auslastung zeigt, wie viele Tickets aktuell in Bearbeitung sind."
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"{guild.name} Support Team")

        if not categories:
            embed.add_field(
                name="⚠️ Hinweis",
                value="Es wurden noch keine Kategorien erstellt. Ein Admin muss `[p]ticket addcat` nutzen.",
                inline=False,
            )
            return embed

        if not conf.get("show_category_stats", True):
            embed.add_field(name="Aktuell offene Tickets", value=str(len(active_tickets)), inline=False)
            return embed

        use_charts = conf.get("use_emoji_charts", True)
        for cat_id, cat_data in categories.items():
            active_count = sum(1 for t in active_tickets if t.get("cat_id") == cat_id)
            max_tickets = cat_data.get("max_tickets", 10)
            emoji = _sanitize_emoji(cat_data.get("emoji"))
            name = str(cat_data.get("name", "Unbekannt"))[:50]
            if max_tickets and max_tickets > 0:
                percent = min(100, int((active_count / max_tickets) * 100))
                if use_charts:
                    status = f"{active_count}/{max_tickets} {self._emoji_bar(active_count, max_tickets)} {percent}%"
                else:
                    status = f"{active_count}/{max_tickets} ({percent}%)"
            else:
                status = f"{active_count} aktiv (unbegrenzt)"
            embed.add_field(name=f"{emoji} {name}", value=f"`{status}`"[:1024], inline=True)
        return embed

    async def update_panels(self, guild: discord.Guild):
        """Aktualisiert alle Panel-Nachrichten eines Servers."""
        try:
            categories = await self.config.guild(guild).categories()
            active_tickets = await self.config.guild(guild).active_tickets()
            panels = await self.config.guild(guild).panels()
            valid = []
            for panel in panels:
                channel = guild.get_channel(panel.get("channel_id"))
                if not channel:
                    continue
                try:
                    message = await channel.fetch_message(panel["msg_id"])
                    view = self._make_panel_view(categories, active_tickets)
                    embed = await self._build_panel_embed(guild, categories, active_tickets)
                    await message.edit(embed=embed, view=view)
                    self.bot.add_view(view, message_id=message.id)
                    valid.append(panel)
                    await asyncio.sleep(0.2)
                except discord.HTTPException:
                    continue
                except Exception:
                    log.exception("Panel-Update fehlgeschlagen")
            await self.config.guild(guild).panels.set(valid)
        except Exception:
            log.exception("update_panels fehlgeschlagen")

    async def create_panel(self, channel: discord.TextChannel):
        """Postet ein neues Ticket-Panel und registriert es."""
        guild = channel.guild
        categories = await self.config.guild(guild).categories()
        if not categories:
            return None
        active_tickets = await self.config.guild(guild).active_tickets()
        view = self._make_panel_view(categories, active_tickets)
        embed = await self._build_panel_embed(guild, categories, active_tickets)
        message = await channel.send(embed=embed, view=view)
        panels = await self.config.guild(guild).panels()
        panels.append({"channel_id": channel.id, "msg_id": message.id})
        await self.config.guild(guild).panels.set(panels)
        return message

    # -- Setup-Speicherung -------------------------------------------------------

    async def finish_base_setup(self, interaction: discord.Interaction, wizard: BaseSetupView):
        guild = interaction.guild
        guild_conf = self.config.guild(guild)
        await guild_conf.log_channel_id.set(wizard.log_channel_id)
        await guild_conf.admin_role_id.set(wizard.admin_role_id)
        await guild_conf.dm_notifications.set(wizard.dm_notifications)
        await guild_conf.autoclose_hours.set(wizard.autoclose_hours)
        await guild_conf.cooldown_minutes.set(wizard.cooldown_minutes)
        await guild_conf.max_tickets_per_user.set(wizard.max_tickets_per_user)
        await guild_conf.delete_threads_after_close.set(wizard.delete_threads_after_close)
        await guild_conf.auto_escalate_hours.set(wizard.auto_escalate_hours)
        await guild_conf.show_category_stats.set(wizard.show_category_stats)
        await guild_conf.use_emoji_charts.set(wizard.use_emoji_charts)
        await interaction.response.edit_message(
            content=(
                "✅ **Setup abgeschlossen!** Erstelle jetzt Kategorien mit `[p]ticket addcat` "
                "und poste ein Panel mit `[p]ticket panel`."
            ),
            view=None,
        )

    async def save_category(self, interaction: discord.Interaction, wizard: CategorySetupView, cat_id=None):
        guild = interaction.guild
        if not cat_id:
            cat_id = str(uuid.uuid4())[:8]
        data = {
            "name": (wizard.name or "").strip(),
            "description": (wizard.description or "").strip() or None,
            "emoji": _sanitize_emoji(wizard.emoji),
            "abbr": (wizard.abbr or "TICKET").strip().upper()[:10],
            "discord_category_id": wizard.discord_category_id,
            "thread_parent_id": wizard.thread_parent_id,
            "staff_role_id": wizard.staff_role_id,
            "high_team_role_id": wizard.high_team_role_id,
            "max_tickets": int(wizard.max_tickets or 0),
        }
        categories = await self.config.guild(guild).categories()
        categories[cat_id] = data
        await self.config.guild(guild).categories.set(categories)

        # Rollen-Referenzen in laufenden Tickets aktualisieren
        tickets = await self.config.guild(guild).active_tickets()
        changed = False
        for ticket in tickets:
            if ticket.get("cat_id") == cat_id:
                ticket["staff_role_id"] = data["staff_role_id"]
                ticket["high_team_role_id"] = data["high_team_role_id"]
                changed = True
        if changed:
            await self.config.guild(guild).active_tickets.set(tickets)

        await self.update_panels(guild)
        await interaction.response.edit_message(content="✅ Kategorie gespeichert!", view=None)

    # -- Ticketerstellung ----------------------------------------------------------

    async def create_ticket(self, interaction: discord.Interaction, cat_id: str, issue: str):
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.InteractionResponded:
            pass
        except Exception:
            pass

        guild = interaction.guild
        user = interaction.user
        conf = await self.config.guild(guild).all()
        cat_data = conf.get("categories", {}).get(cat_id)
        if not cat_data:
            return await interaction.followup.send("❌ Kategorie nicht gefunden.", ephemeral=True)
        if user.id in (conf.get("blacklist") or []):
            return await interaction.followup.send("❌ Du bist vom Ticket-System gesperrt.", ephemeral=True)

        active = conf.get("active_tickets", [])
        max_tickets = conf.get("max_tickets_per_user", 1)
        if len([t for t in active if t.get("user_id") == user.id]) >= max_tickets:
            return await interaction.followup.send(
                f"❌ Du hast bereits das Maximum von {max_tickets} offenen Ticket(s) erreicht.",
                ephemeral=True,
            )
        max_cat = cat_data.get("max_tickets", 10)
        if max_cat and max_cat > 0:
            if len([t for t in active if t.get("cat_id") == cat_id]) >= max_cat:
                return await interaction.followup.send(
                    "❌ Diese Kategorie ist aktuell ausgelastet.", ephemeral=True
                )

        staff_role = guild.get_role(cat_data.get("staff_role_id")) if cat_data.get("staff_role_id") else None
        high_role = guild.get_role(cat_data.get("high_team_role_id")) if cat_data.get("high_team_role_id") else None
        admin_role = guild.get_role(conf.get("admin_role_id")) if conf.get("admin_role_id") else None

        number = await self.config.guild(guild).ticket_counter() + 1
        await self.config.guild(guild).ticket_counter.set(number)

        channel_name = f"{cat_data.get('abbr', 'TICKET')}-{user.name}-{number:04d}"[:100]
        now_iso = _utcnow().isoformat()
        ticket_channel = None

        try:
            if cat_data.get("thread_parent_id"):
                parent = guild.get_channel(cat_data["thread_parent_id"])
                if not parent or not isinstance(parent, discord.TextChannel):
                    raise ValueError("Der konfigurierte Thread-Channel existiert nicht mehr.")
                if not parent.permissions_for(guild.me).create_private_threads:
                    raise ValueError(
                        f"Mir fehlt die Berechtigung 'Private Threads erstellen' in {parent.mention}."
                    )
                # 'Threads verwalten' absichern, damit Archivieren/Sperren
                # später zuverlässig funktioniert
                try:
                    if not parent.permissions_for(guild.me).manage_threads:
                        await parent.set_permissions(guild.me, manage_threads=True)
                except discord.HTTPException:
                    log.warning(
                        "Konnte 'Threads verwalten' für mich in #%s nicht setzen – "
                        "die spätere Archivierung könnte fehlschlagen.",
                        parent.name,
                    )
                ticket_channel = await parent.create_thread(
                    name=channel_name,
                    type=discord.ChannelType.private_thread,
                    auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES,
                )
                try:
                    await ticket_channel.add_user(user)
                except discord.HTTPException:
                    pass
                await self._add_role_members_to_thread(ticket_channel, [staff_role, high_role, admin_role])
            elif cat_data.get("discord_category_id"):
                category = guild.get_channel(cat_data["discord_category_id"])
                if not category or not isinstance(category, discord.CategoryChannel):
                    raise ValueError("Die konfigurierte Discord-Kategorie existiert nicht mehr.")
                if not guild.me.guild_permissions.manage_channels:
                    raise ValueError("Mir fehlt die Berechtigung 'Kanäle verwalten'.")
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    user: discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                        attach_files=True,
                        embed_links=True,
                    ),
                    guild.me: discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        manage_channels=True,
                        manage_messages=True,
                        read_message_history=True,
                        attach_files=True,
                        embed_links=True,
                    ),
                }
                for role in (staff_role, high_role, admin_role):
                    if role:
                        overwrites[role] = discord.PermissionOverwrite(
                            view_channel=True,
                            send_messages=True,
                            read_message_history=True,
                            attach_files=True,
                            embed_links=True,
                        )
                ticket_channel = await guild.create_text_channel(
                    name=channel_name, category=category, overwrites=overwrites
                )
            else:
                raise ValueError(
                    "Für diese Kategorie ist weder eine Discord-Kategorie noch ein Thread-Channel konfiguriert."
                )
        except Exception as error:
            log.exception("Ticketerstellung fehlgeschlagen")
            error_text = str(error) or error.__class__.__name__
            await self.send_log(
                guild,
                "❌ Fehler bei Ticketerstellung",
                discord.Color.red(),
                [
                    ("User", user.mention),
                    ("Kategorie", cat_data.get("name")),
                    ("Fehler", error_text[:1000]),
                ],
            )
            return await interaction.followup.send(
                f"❌ Ticket konnte nicht erstellt werden: {error_text[:400]}", ephemeral=True
            )

        ticket_data = {
            "channel_id": ticket_channel.id,
            "user_id": user.id,
            "cat_id": cat_id,
            "number": number,
            "issue": issue[:1500],
            "last_message": now_iso,
            "created_at": now_iso,
            "claimed_by": None,
            "escalated": False,
            "status": "ACTIVE",
            "panel_msg_id": None,
            "first_response_at": None,
            "first_responder_id": None,
            "staff_role_id": cat_data.get("staff_role_id"),
            "high_team_role_id": cat_data.get("high_team_role_id"),
        }

        try:
            embed = self._build_control_embed(ticket_data, cat_data)
            mentions = user.mention + (f" {staff_role.mention}" if staff_role else "")
            view = TicketControlView(self)
            message = await ticket_channel.send(content=mentions, embed=embed, view=view)
            ticket_data["panel_msg_id"] = message.id
        except Exception:
            log.exception("Ticket-Begrüßung fehlgeschlagen")
            try:
                await ticket_channel.delete()
            except Exception:
                pass
            return await interaction.followup.send(
                "❌ Fehler beim Erstellen des Tickets (Begrüßung). Der Kanal wurde wieder entfernt.",
                ephemeral=True,
            )

        tickets = await self.config.guild(guild).active_tickets()
        tickets.append(ticket_data)
        await self.config.guild(guild).active_tickets.set(tickets)

        total = await self.config.guild(guild).total_tickets_created()
        await self.config.guild(guild).total_tickets_created.set(total + 1)

        cat_stats = await self.config.guild(guild).category_stats()
        cs = cat_stats.get(
            cat_id, {"created": 0, "closed": 0, "stars": [0, 0, 0, 0, 0], "total_duration_minutes": 0, "ticket_count": 0}
        )
        cs["created"] = cs.get("created", 0) + 1
        cat_stats[cat_id] = cs
        await self.config.guild(guild).category_stats.set(cat_stats)

        self._add_to_active_cache(guild.id, ticket_channel.id)

        if conf.get("dm_notifications"):
            await self.send_dm(
                user,
                "🎫 Ticket erstellt",
                f"Dein Ticket **#{number:04d}** ({cat_data.get('name')}) wurde erstellt:\n{ticket_channel.mention}",
            )
        await self.send_log(
            guild,
            "🎫 Ticket eröffnet",
            discord.Color.green(),
            [
                ("Ticket", f"#{number:04d}"),
                ("User", user.mention),
                ("Kategorie", cat_data.get("name")),
                ("Kanal", ticket_channel.mention),
            ],
        )
        await self.update_panels(guild)
        await interaction.followup.send(
            f"✅ Ticket **#{number:04d}** erstellt: {ticket_channel.mention}", ephemeral=True
        )

    # -- Ticket-Steuerung: Übernehmen / Eskalieren / Status ---------------------------

    def _build_control_embed(self, ticket_data: dict, cat_data: dict) -> discord.Embed:
        number = ticket_data.get("number") or 0
        status = TICKET_STATUS.get(ticket_data.get("status", "ACTIVE"), TICKET_STATUS["ACTIVE"])
        emoji = _sanitize_emoji(cat_data.get("emoji"))
        cat_name = str(cat_data.get("name", "Support"))
        issue = ticket_data.get("issue") or "*(Kein Anliegen gespeichert)*"
        created = _parse_dt(ticket_data.get("created_at"))
        duration = _fmt_duration((_utcnow() - created).total_seconds() / 60)
        claimed_by = ticket_data.get("claimed_by")

        if number:
            title = f"{emoji} Ticket #{int(number):04d} – {cat_name}"
        else:
            title = f"{emoji} Ticket – {cat_name}"

        embed = discord.Embed(title=title[:256], description=f"**Anliegen:**\n{issue[:1500]}", color=status["color"])
        embed.add_field(name="Ersteller", value=f"<@{ticket_data.get('user_id')}>", inline=True)
        embed.add_field(name="Status", value=f"{status['emoji']} {status['label']}", inline=True)
        embed.add_field(
            name="Übernommen von", value=f"<@{claimed_by}>" if claimed_by else "—", inline=True
        )
        embed.add_field(name="Eskaliert", value="⚠️ Ja" if ticket_data.get("escalated") else "Nein", inline=True)
        embed.add_field(name="Erstellt", value=discord.utils.format_dt(created, style="f"), inline=True)
        embed.add_field(name="Offen seit", value=duration, inline=True)
        embed.set_footer(text=f"Ticket #{int(number):04d}" if number else "Ticket")
        return embed

    async def _update_control_embed(self, channel, ticket_data: dict):
        if not ticket_data.get("panel_msg_id"):
            return
        try:
            categories = await self.config.guild(channel.guild).categories()
            cat_data = categories.get(ticket_data.get("cat_id")) or {"name": "Support", "emoji": "🎫"}
            message = await channel.fetch_message(ticket_data["panel_msg_id"])
            await message.edit(embed=self._build_control_embed(ticket_data, cat_data))
        except discord.HTTPException:
            pass
        except Exception:
            log.debug("Control-Embed-Update fehlgeschlagen", exc_info=True)

    @staticmethod
    def _set_claim_button(view: TicketControlView, claimed: bool):
        for child in view.children:
            if getattr(child, "custom_id", None) == "support_ticket_claim_btn":
                child.label = "Freigeben" if claimed else "Übernehmen"
                child.style = (
                    discord.ButtonStyle.secondary if claimed else discord.ButtonStyle.success
                )

    async def claim_ticket(self, interaction: discord.Interaction, view: TicketControlView):
        guild = interaction.guild
        tickets = await self.config.guild(guild).active_tickets()
        ticket = self._find_ticket(tickets, interaction.channel.id)
        if not ticket:
            return await interaction.response.send_message(
                "❌ Kein aktives Ticket in diesem Kanal.", ephemeral=True
            )
        if interaction.user.id == ticket.get("user_id"):
            return await interaction.response.send_message(
                "❌ Du bist der Ersteller dieses Tickets.", ephemeral=True
            )
        if not await self.is_support(interaction.user, guild, ticket):
            return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)

        claimed_by = ticket.get("claimed_by")
        channel_id = ticket["channel_id"]

        if claimed_by == interaction.user.id:
            # Freigeben
            for entry in tickets:
                if entry.get("channel_id") == channel_id:
                    entry["claimed_by"] = None
                    break
            await self.config.guild(guild).active_tickets.set(tickets)
            self._set_claim_button(view, claimed=False)
            await interaction.response.edit_message(view=view)
            await interaction.channel.send(f"🔓 {interaction.user.mention} hat das Ticket wieder freigegeben.")
            ticket["claimed_by"] = None
            await self._update_control_embed(interaction.channel, ticket)
            return

        if claimed_by:
            return await interaction.response.send_message(
                f"❌ Dieses Ticket wurde bereits von <@{claimed_by}> übernommen.", ephemeral=True
            )

        for entry in tickets:
            if entry.get("channel_id") == channel_id:
                entry["claimed_by"] = interaction.user.id
                break
        await self.config.guild(guild).active_tickets.set(tickets)

        stats = await self.config.guild(guild).stats()
        user_stats = stats.get(
            str(interaction.user.id), {"claimed": 0, "closed": 0, "stars": [0, 0, 0, 0, 0]}
        )
        user_stats["claimed"] = user_stats.get("claimed", 0) + 1
        stats[str(interaction.user.id)] = user_stats
        await self.config.guild(guild).stats.set(stats)

        self._set_claim_button(view, claimed=True)
        await interaction.response.edit_message(view=view)
        await interaction.channel.send(f"✋ {interaction.user.mention} hat das Ticket übernommen.")
        ticket["claimed_by"] = interaction.user.id
        await self._update_control_embed(interaction.channel, ticket)
        await self.send_log(
            guild,
            "✋ Ticket übernommen",
            discord.Color.blurple(),
            [("Ticket", _ticket_label(ticket)), ("Von", interaction.user.mention)],
        )

    async def escalate_ticket(self, interaction: discord.Interaction, view: TicketControlView):
        guild = interaction.guild
        tickets = await self.config.guild(guild).active_tickets()
        ticket = self._find_ticket(tickets, interaction.channel.id)
        if not ticket:
            return await interaction.response.send_message(
                "❌ Kein aktives Ticket in diesem Kanal.", ephemeral=True
            )
        if interaction.user.id == ticket.get("user_id"):
            return await interaction.response.send_message(
                "❌ Du bist der Ersteller dieses Tickets.", ephemeral=True
            )
        if not await self.is_support(interaction.user, guild, ticket):
            return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
        if ticket.get("escalated"):
            return await interaction.response.send_message(
                "⚠️ Dieses Ticket wurde bereits eskaliert.", ephemeral=True
            )

        categories = await self.config.guild(guild).categories()
        cat_data = categories.get(ticket.get("cat_id"), {}) or {}
        high_role = guild.get_role(cat_data.get("high_team_role_id")) if cat_data.get("high_team_role_id") else None
        if not high_role:
            return await interaction.response.send_message(
                "❌ Für diese Kategorie ist keine High-Team-Rolle konfiguriert.", ephemeral=True
            )

        channel_id = ticket["channel_id"]
        for entry in tickets:
            if entry.get("channel_id") == channel_id:
                entry["escalated"] = True
                entry["claimed_by"] = None
                break
        await self.config.guild(guild).active_tickets.set(tickets)

        # Zugang für das High-Team sicherstellen
        try:
            if isinstance(interaction.channel, discord.Thread):
                await self._add_role_members_to_thread(interaction.channel, [high_role])
            else:
                await interaction.channel.set_permissions(
                    high_role,
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                )
        except discord.HTTPException:
            log.warning("Konnte High-Team-Zugang bei Eskalation nicht setzen.")

        for child in view.children:
            if getattr(child, "custom_id", None) == "support_ticket_escalate_btn":
                child.disabled = True
        self._set_claim_button(view, claimed=False)
        await interaction.response.edit_message(view=view)
        await interaction.channel.send(
            f"⚠️ {interaction.user.mention} hat dieses Ticket eskaliert: {high_role.mention}"
        )
        ticket["escalated"] = True
        ticket["claimed_by"] = None
        await self._update_control_embed(interaction.channel, ticket)
        await self.send_log(
            guild,
            "⚠️ Ticket eskaliert",
            discord.Color.orange(),
            [
                ("Ticket", _ticket_label(ticket)),
                ("Von", interaction.user.mention),
                ("High-Team", high_role.mention),
            ],
        )
        conf = await self.config.guild(guild).dm_notifications()
        if conf:
            creator = guild.get_member(ticket.get("user_id"))
            await self.send_dm(
                creator,
                "⚠️ Dein Ticket wurde eskaliert",
                "Dein Ticket wurde an das High-Team eskaliert. Bitte habe noch etwas Geduld.",
            )

    async def change_status(self, interaction: discord.Interaction, status: str, view: TicketControlView):
        if status not in TICKET_STATUS:
            return await interaction.response.send_message("❌ Ungültiger Status.", ephemeral=True)
        guild = interaction.guild
        tickets = await self.config.guild(guild).active_tickets()
        ticket = self._find_ticket(tickets, interaction.channel.id)
        if not ticket:
            return await interaction.response.send_message(
                "❌ Kein aktives Ticket in diesem Kanal.", ephemeral=True
            )
        if interaction.user.id == ticket.get("user_id"):
            return await interaction.response.send_message(
                "❌ Du bist der Ersteller dieses Tickets.", ephemeral=True
            )
        if not await self.is_support(interaction.user, guild, ticket):
            return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)

        for entry in tickets:
            if entry.get("channel_id") == ticket["channel_id"]:
                entry["status"] = status
                break
        await self.config.guild(guild).active_tickets.set(tickets)

        status_info = TICKET_STATUS[status]
        await interaction.response.send_message(
            f"✅ Status geändert: {status_info['emoji']} **{status_info['label']}**", ephemeral=True
        )
        ticket["status"] = status
        await self._update_control_embed(interaction.channel, ticket)

        if status == "WAITING_USER":
            dm_enabled = await self.config.guild(guild).dm_notifications()
            if dm_enabled:
                creator = guild.get_member(ticket.get("user_id"))
                await self.send_dm(
                    creator,
                    "🟡 Das Team wartet auf dich",
                    f"Das Team wartet auf deine Antwort in Ticket {_ticket_label(ticket)}.",
                )

    # -- Schließen, Transkript & Aufräumen ---------------------------------------------

    async def close_ticket(
        self,
        channel,
        reason: str,
        user,
        interaction: discord.Interaction | None = None,
        is_auto: bool = False,
        force_delete: bool = False,
    ):
        guild = channel.guild
        conf = await self.config.guild(guild).all()
        ticket = self._find_ticket(conf.get("active_tickets", []), channel.id)
        if ticket is None:
            if interaction is not None:
                await _respond_error(interaction, "❌ Kein aktives Ticket in diesem Kanal.")
            return

        number = ticket.get("number") or 0
        closer_is_creator = user.id == ticket.get("user_id")
        closer_is_support = (
            await self.is_support(user, guild, ticket) if isinstance(user, discord.Member) else bool(is_auto)
        )
        now = _utcnow()
        created = _parse_dt(ticket.get("created_at"))
        duration_minutes = (now - created).total_seconds() / 60

        closed_by = None
        if not is_auto:
            if ticket.get("claimed_by"):
                closed_by = ticket["claimed_by"]
            elif closer_is_support and not closer_is_creator:
                closed_by = user.id

        # Statistiken
        try:
            stats = conf.get("stats", {})
            if closed_by is not None:
                closer_stats = stats.get(
                    str(closed_by), {"claimed": 0, "closed": 0, "stars": [0, 0, 0, 0, 0]}
                )
                closer_stats["closed"] = closer_stats.get("closed", 0) + 1
                closer_stats["total_duration_minutes"] = (
                    closer_stats.get("total_duration_minutes", 0) + duration_minutes
                )
                closer_stats["ticket_count"] = closer_stats.get("ticket_count", 0) + 1
                stats[str(closed_by)] = closer_stats
            if ticket.get("first_response_at") and ticket.get("first_responder_id"):
                responder_key = str(ticket["first_responder_id"])
                responder_stats = stats.get(
                    responder_key, {"claimed": 0, "closed": 0, "stars": [0, 0, 0, 0, 0]}
                )
                reaction_minutes = (
                    _parse_dt(ticket["first_response_at"]) - created
                ).total_seconds() / 60
                if reaction_minutes >= 0:
                    responder_stats["total_reaction_minutes"] = (
                        responder_stats.get("total_reaction_minutes", 0) + reaction_minutes
                    )
                    responder_stats["reaction_count"] = responder_stats.get("reaction_count", 0) + 1
                stats[responder_key] = responder_stats
            await self.config.guild(guild).stats.set(stats)

            cat_stats = conf.get("category_stats", {})
            cat_entry = cat_stats.get(
                ticket.get("cat_id"),
                {"created": 0, "closed": 0, "stars": [0, 0, 0, 0, 0], "total_duration_minutes": 0, "ticket_count": 0},
            )
            cat_entry["closed"] = cat_entry.get("closed", 0) + 1
            cat_entry["total_duration_minutes"] = (
                cat_entry.get("total_duration_minutes", 0) + duration_minutes
            )
            cat_entry["ticket_count"] = cat_entry.get("ticket_count", 0) + 1
            cat_stats[ticket.get("cat_id")] = cat_entry
            await self.config.guild(guild).category_stats.set(cat_stats)
        except Exception:
            log.exception("Fehler beim Speichern der Statistiken")

        # Verlauf
        try:
            history_entry = {
                "user_id": ticket.get("user_id"),
                "cat_id": ticket.get("cat_id"),
                "channel_id": channel.id,
                "number": number,
                "created_at": ticket.get("created_at"),
                "closed_at": now.isoformat(),
                "close_reason": reason,
                "closed_by": closed_by,
                "stars": 0,
            }
            history = await self.config.guild(guild).ticket_history()
            history.append(history_entry)
            if len(history) > HISTORY_LIMIT:
                history = history[-HISTORY_LIMIT:]
            await self.config.guild(guild).ticket_history.set(history)
        except Exception:
            log.exception("Fehler beim Speichern der Historie")

        # Transkript
        html_content = None
        message_count = 0
        closer_html = "Auto-Close (Inaktivität)" if is_auto else (
            f"{_escape_html(user.display_name)}" if user else "Unbekannt"
        )
        try:
            html_content, message_count = await self._build_transcript(
                channel, ticket, reason, closer_html, duration_minutes
            )
        except Exception:
            log.exception("Transkript fehlgeschlagen")

        # Log-Channel
        try:
            log_channel = (
                guild.get_channel(conf.get("log_channel_id")) if conf.get("log_channel_id") else None
            )
            if log_channel:
                categories = conf.get("categories", {})
                cat_name = (categories.get(ticket.get("cat_id"), {}) or {}).get("name", "Unbekannt")
                log_embed = discord.Embed(
                    title="🔒 Ticket geschlossen", color=discord.Color.red(), timestamp=now
                )
                log_embed.add_field(name="Ticket", value=f"#{number:04d}" if number else channel.name, inline=True)
                log_embed.add_field(name="Kategorie", value=cat_name, inline=True)
                log_embed.add_field(name="Nachrichten", value=str(message_count), inline=True)
                log_embed.add_field(name="Ersteller", value=f"<@{ticket.get('user_id')}>", inline=True)
                log_embed.add_field(
                    name="Geschlossen von",
                    value="Auto-Close" if is_auto else f"<@{user.id}>",
                    inline=True,
                )
                log_embed.add_field(name="Dauer", value=_fmt_duration(duration_minutes), inline=True)
                log_embed.add_field(name="Grund", value=str(reason)[:1024], inline=False)
                transcript_file = None
                if html_content:
                    transcript_file = discord.File(
                        io.StringIO(html_content),
                        filename=f"transcript-{number:04d}-{channel.id}.html",
                    )
                await log_channel.send(embed=log_embed, file=transcript_file)
        except Exception:
            log.exception("Log-Versand fehlgeschlagen")

        # DM an Ersteller
        try:
            if conf.get("dm_notifications"):
                recipient = guild.get_member(ticket.get("user_id")) or self.bot.get_user(
                    ticket.get("user_id")
                )
                if recipient:
                    dm_embed = discord.Embed(
                        title="🔒 Dein Ticket wurde geschlossen", color=discord.Color.red()
                    )
                    dm_embed.add_field(name="Grund", value=str(reason)[:1024], inline=False)
                    dm_embed.add_field(name="Dauer", value=_fmt_duration(duration_minutes), inline=False)
                    dm_file = None
                    if html_content:
                        dm_file = discord.File(
                            io.StringIO(html_content),
                            filename=f"transcript-{number:04d}-{channel.id}.html",
                        )
                    await recipient.send(embed=dm_embed, file=dm_file)
        except Exception:
            log.debug("Abschluss-DM fehlgeschlagen (vermutlich blockiert).", exc_info=True)

        # Aus aktiven Tickets entfernen
        await self._remove_ticket(guild, channel.id)

        if interaction is not None:
            await _respond_error(interaction, "✅ Ticket geschlossen.")

        if is_auto or force_delete:
            await self.delete_ticket_channel(channel, ticket, 0)
        else:
            try:
                closer_text = "Auto-Close" if is_auto else f"<@{user.id}>"
                await channel.send(
                    f"🔒 Dieses Ticket wurde von {closer_text} geschlossen.\n**Grund:** {reason}"
                )
            except Exception:
                pass
            try:
                review_view = ReviewView(self, ticket)
                review_message = await channel.send(
                    content=(
                        f"⭐ <@{ticket.get('user_id')}>, wie war der Support? "
                        "Bitte bewerte mit 1–5 Sternen."
                    ),
                    view=review_view,
                )
                review_view.message = review_message
            except Exception:
                log.exception("Bewertungs-View fehlgeschlagen – Kanal wird direkt aufgeräumt.")
                await self.delete_ticket_channel(channel, ticket, 0)

        await self.update_panels(guild)

    async def _build_transcript(self, channel, ticket: dict, reason: str, closer_html: str, duration_minutes: float):
        guild = channel.guild
        conf = await self.config.guild(guild).all()
        cat_data = conf.get("categories", {}).get(ticket.get("cat_id"), {}) or {}
        parts = []
        count = 0
        async for message in channel.history(limit=None, oldest_first=True):
            count += 1
            content = _escape_html(message.content) if message.content else ""
            if message.attachments:
                links = ", ".join(
                    f'<a href="{a.url}">{_escape_html(a.filename)}</a>'
                    for a in message.attachments
                )
                content += f"<br><i>Anhänge: {links}</i>"
            if not content:
                content = "<i>(leer)</i>"
            color = getattr(message.author, "color", None)
            color_hex = f"#{color.value:06x}" if color and color.value else "#ffffff"
            parts.append(
                MESSAGE_HTML.format(
                    avatar_url=message.author.display_avatar.url,
                    author=_escape_html(message.author.display_name),
                    color=color_hex,
                    timestamp=_parse_dt(message.created_at).astimezone().strftime("%d.%m.%Y %H:%M"),
                    content=content,
                )
            )

        number = ticket.get("number") or 0
        creator = guild.get_member(ticket.get("user_id"))
        creator_name = (
            _escape_html(creator.display_name)
            if creator
            else _escape_html(str(ticket.get("user_id")))
        )
        html_content = HTML_TEMPLATE.format(
            channel_name=_escape_html(channel.name),
            ticket_number=f"{number:04d}" if number else "—",
            category=_escape_html(cat_data.get("name", "Unbekannt")),
            creator=creator_name,
            created_at=_parse_dt(ticket.get("created_at")).astimezone().strftime("%d.%m.%Y %H:%M"),
            closed_at=_utcnow().astimezone().strftime("%d.%m.%Y %H:%M"),
            close_reason=_escape_html(reason),
            duration=_fmt_duration(duration_minutes),
            closer=closer_html,
            message_count=count,
            messages_html="".join(parts),
            version=__version__,
        )
        return html_content, count

    async def delete_ticket_channel(self, channel, ticket_data: dict, stars: int):
        guild = channel.guild
        conf = await self.config.guild(guild).all()

        # Sterne in Statistik & Verlauf erfassen
        try:
            if stars > 0 and ticket_data.get("claimed_by"):
                stats = conf.get("stats", {})
                claimer_key = str(ticket_data["claimed_by"])
                claimer_stats = stats.get(
                    claimer_key, {"claimed": 0, "closed": 0, "stars": [0, 0, 0, 0, 0]}
                )
                star_list = claimer_stats.setdefault("stars", [0, 0, 0, 0, 0])
                while len(star_list) < 5:
                    star_list.append(0)
                star_list[stars - 1] += 1
                stats[claimer_key] = claimer_stats
                await self.config.guild(guild).stats.set(stats)
        except Exception:
            log.exception("Sterne konnten nicht gespeichert werden")

        try:
            history = await self.config.guild(guild).ticket_history()
            for entry in reversed(history):
                if entry.get("channel_id") == channel.id:
                    entry["stars"] = stars
                    break
            await self.config.guild(guild).ticket_history.set(history)
        except Exception:
            log.exception("Verlauf konnte nicht aktualisiert werden")

        await self._remove_ticket(guild, channel.id)

        # Steuerungs-Buttons deaktivieren
        try:
            if ticket_data.get("panel_msg_id"):
                control_message = await channel.fetch_message(ticket_data["panel_msg_id"])
                await control_message.edit(view=None)
        except Exception:
            pass

        try:
            if isinstance(channel, discord.Thread):
                if conf.get("delete_threads_after_close", False):
                    await channel.delete()
                else:
                    cat_data = (conf.get("categories", {}) or {}).get(ticket_data.get("cat_id"))
                    await self._archive_ticket_thread(channel, guild, cat_data)
            else:
                await channel.delete()
        except Exception:
            log.exception("Fehler beim Aufräumen des Ticket-Kanals")

        await self.update_panels(guild)

    # -- Thread-Helfer ---------------------------------------------------------------

    async def _set_thread_archived(self, thread: discord.Thread, archived: bool):
        """Archiviert/ent-archiviert einen Thread und sperrt ihn optional (Best Effort).

        Das Sperren erfordert 'Threads verwalten' im Parent-Channel; fehlt diese
        Berechtigung, wird immerhin archiviert und eine Warnung geloggt.
        """
        try:
            await thread.edit(archived=archived, locked=archived)
        except discord.Forbidden:
            try:
                await thread.edit(archived=archived)
                log.warning(
                    "Thread #%s wurde %s, aber nicht gesperrt – mir fehlt "
                    "'Threads verwalten' im Parent-Channel.",
                    thread.name,
                    "archiviert" if archived else "ent-archiviert",
                )
            except discord.HTTPException:
                raise

    async def _archive_ticket_thread(
        self, thread: discord.Thread, guild: discord.Guild, cat_data: dict | None
    ):
        """Archiviert einen Ticket-Thread zuverlässig.

        Reihenfolge: ent-archivieren -> Rollen-Mitglieder synchronisieren ->
        'archiv-'-Präfix -> archivieren + sperren. Jeder Schritt ist einzeln
        abgesichert, damit ein Fehler nicht die komplette Archivierung verhindert.
        """
        # 1) Ent-archivieren (falls nötig)
        try:
            if thread.archived or thread.locked:
                await self._set_thread_archived(thread, False)
        except discord.HTTPException:
            log.warning("Konnte Thread #%s nicht ent-archivieren.", thread.name)

        # 2) Rollen-Mitglieder synchronisieren (Support/High-Team/Admin)
        try:
            await self._sync_roles_to_thread(thread, guild, cat_data=cat_data)
        except Exception:
            log.exception("Rollen-Sync vor der Archivierung fehlgeschlagen")

        # 3) Umbenennen (nur moeglich, solange nicht archiviert). Prefix
        #    abschnappen, falls der Thread bereits archiviert war und erneut
        #    aufgerufen wird – sonst entstuende 'archiv-archiv-...'.
        base_name = thread.name
        if base_name.lower().startswith("archiv-"):
            base_name = base_name[7:]
        new_name = f"archiv-{base_name}"[:100]
        if thread.name != new_name:
            try:
                await thread.edit(name=new_name)
            except discord.HTTPException:
                log.warning("Konnte Thread #%s nicht umbenennen.", thread.id)

        # 4) Archivieren + sperren
        try:
            await self._set_thread_archived(thread, True)
        except discord.HTTPException:
            log.exception("Konnte Thread #%s nicht archivieren", thread.id)

    async def _ensure_guild_chunked(self, guild: discord.Guild) -> bool:
        """Stellt sicher, dass der Member-Cache des Servers vollstaendig ist.

        Gibt True zurueck, wenn der Cache vollstaendig ist (oder erfolgreich
        aufgefuellt werden konnte). Gibt False zurueck, wenn der Cache
        unvollstaendig bleibt – typischerweise, weil der Bot den *SERVER
        MEMBERS INTENT* (privileged gateway intent) nicht aktiviert hat.
        In diesem Fall werden nur bereits gecachte Mitglieder zu Threads
        hinzugefuegt (typischerweise nur wenige) – genau das Symptom
        "es werden nur ein paar Leute hinzugefuegt".
        """
        if guild.chunked:
            return True
        try:
            await guild.chunk()
        except Exception as exc:
            # Einmalig pro Guild pro Lauf warnen – sonst spammt es das Log voll
            if guild.id not in self._warned_chunk_guilds:
                self._warned_chunk_guilds.add(guild.id)
                log.error(
                    "Konnte Mitglieder fuer Guild '%s' (%s) nicht abrufen: %s. "
                    "Bitte in Discord: Developer Portal -> Bot -> Privileged "
                    "Gateway Intents -> SERVER MEMBERS INTENT aktivieren, "
                    "UND in Red mit `[p]set privilegedintents on` "
                    "freischalten. Solange das nicht passt, werden nur die "
                    "bereits gecachten Mitglieder zu Ticket-Threads "
                    "hinzugefuegt.",
                    getattr(guild, "name", "?"),
                    guild.id,
                    exc,
                )
                await self.send_log(
                    guild,
                    "Mitglieder-Intent fehlt",
                    discord.Color.red(),
                    [
                        ("Problem", "Der Bot kann die Mitgliederliste des Servers nicht abrufen."),
                        ("Folge", "Es werden nur wenige (gecachte) Mitglieder zu Ticket-Threads hinzugefuegt – Support/High-Team sehen Tickets ggf. nicht."),
                        ("Loesung", "Im Discord Developer Portal unter *Bot -> Privileged Gateway Intents* die Option *SERVER MEMBERS INTENT* aktivieren. Danach Red neu starten."),
                    ],
                )
            return bool(guild.chunked)
        return bool(guild.chunked)

    async def _add_role_members_to_thread(self, thread: discord.Thread, roles):
        """Fuegt alle Mitglieder der uebergebenen Rollen zu einem Thread hinzu.

        Verbesserung gegenueber der ersten V1: Vorab wird
        `thread.fetch_members()` abgerufen, damit bereits vorhandene Mitglieder
        uebersprungen werden (spart Rate-Limit und vermeidet stumme
        'already in thread'-Fehler). `add_user` bekommt ein `discord.Object`,
        nicht den Member selbst – schneller und funktioniert auch dann, wenn
        ein Member nur teilweise gecacht ist. Zaehler fuer added/skipped/
        failed werden geloggt, damit das Symptom 'nur ein paar' endlich
        sichtbar wird.
        """
        guild = thread.guild
        await self._ensure_guild_chunked(guild)

        # 1) Bereits vorhandene Thread-Mitglieder abrufen (1 HTTP-Call).
        #    discord.py 2.7.1: Thread.fetch_members() ist eine reguläre
        #    `async def`, die List[ThreadMember] zurückgibt (kein AsyncIter).
        existing_ids: set[int] = set()
        try:
            existing_members = await thread.fetch_members()
            for tm in existing_members:
                # ThreadMember hat Attribut .id (neuer) bzw. .user_id (älter)
                mid = getattr(tm, "id", None) or getattr(tm, "user_id", None)
                if mid is not None:
                    existing_ids.add(int(mid))
        except discord.HTTPException:
            # Fallback: ohne Skip weitermachen – nicht toedlich
            pass

        # 2) Kandidaten aus role.members sammeln (Bot selbst ausnehmen)
        candidates: set[int] = set()
        for role in roles:
            if role is None:
                continue
            for member in role.members:
                if member.id == guild.me.id:
                    continue
                candidates.add(member.id)

        already_in = candidates & existing_ids
        to_add = candidates - existing_ids
        if not to_add:
            if candidates:
                log.info(
                    "Thread #%s: alle %d Kandidaten bereits enthalten, "
                    "kein Add noetig.",
                    thread.name,
                    len(candidates),
                )
            return {"added": 0, "skipped": len(already_in), "failed": 0, "uncached": 0}

        # 3) Paralleles Hinzufuegen mit sanftem Concurrency-Limit; discord.py
        # handhabt 429-Buckets automatisch (der Semaphore begrenzt nur
        # gleichzeitige Anfragen, um den Socket nicht zu fluten).
        semaphore = asyncio.Semaphore(8)
        added = 0
        failed = 0
        failed_uncached = 0

        async def _add(user_id: int) -> None:
            nonlocal added, failed, failed_uncached
            async with semaphore:
                try:
                    await thread.add_user(discord.Object(id=user_id))
                    added += 1
                except discord.HTTPException as exc:
                    if exc.status == 404:
                        failed_uncached += 1
                    elif exc.status == 400 and "already" in str(exc).lower():
                        # Race: in der Zwischenzeit bereits hinzugefuegt
                        added += 1
                    else:
                        failed += 1
                    log.debug(
                        "Thread-Member-Add fuer User %s fehlgeschlagen: %s",
                        user_id,
                        exc,
                    )

        await asyncio.gather(*(_add(uid) for uid in to_add))

        log.info(
            "Thread #%s: %d Mitglieder hinzugefuegt, %d uebersprungen "
            "(bereits drin), %d fehlgeschlagen, %d nicht gefunden (ID nicht "
            "im Cache).",
            thread.name,
            added,
            len(already_in),
            failed,
            failed_uncached,
        )
        return {
            "added": added,
            "skipped": len(already_in),
            "failed": failed,
            "uncached": failed_uncached,
        }

    async def _sync_roles_to_thread(self, thread: discord.Thread, guild: discord.Guild, cat_data: dict | None = None):
        conf = None
        if cat_data is None:
            conf = await self.config.guild(guild).all()
            ticket = self._find_ticket(conf.get("active_tickets", []), thread.id)
            if ticket is None:
                for entry in reversed(conf.get("ticket_history", [])):
                    if entry.get("channel_id") == thread.id:
                        ticket = entry
                        break
            if ticket is not None and ticket.get("cat_id"):
                cat_data = conf.get("categories", {}).get(ticket.get("cat_id"))
        if not cat_data:
            return {"added": 0, "skipped": 0, "failed": 0, "uncached": 0}
        if conf is None:
            conf = await self.config.guild(guild).all()
        roles = []
        for role_id in (cat_data.get("staff_role_id"), cat_data.get("high_team_role_id")):
            role = guild.get_role(role_id) if role_id else None
            if role:
                roles.append(role)
        admin_role = guild.get_role(conf.get("admin_role_id")) if conf.get("admin_role_id") else None
        if admin_role:
            roles.append(admin_role)
        return await self._add_role_members_to_thread(thread, roles)


async def setup(bot: Red):
    await bot.add_cog(SupportCog(bot))
