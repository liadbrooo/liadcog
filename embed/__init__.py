import discord
import uuid
import re
import copy
import logging
from redbot.core import commands, Config

log = logging.getLogger("red.v2builder")

# ============================================================
# SICHERE V2-IMPORTS MIT FEATURE-FLAGS
# ============================================================
V2_AVAILABLE = False
HAS_SECTION = False
HAS_THUMBNAIL = False
HAS_MEDIAGALLERY = False

try:
    from discord.ui import (
        LayoutView, Container, TextDisplay, Separator,
        ActionRow, Button, Modal, TextInput
    )
    V2_AVAILABLE = True
except ImportError:
    from discord.ui import Modal, TextInput, Button

if V2_AVAILABLE:
    try:
        from discord.ui import Section
        HAS_SECTION = True
    except ImportError:
        Section = None

    try:
        from discord.ui import Thumbnail
        HAS_THUMBNAIL = True
    except ImportError:
        Thumbnail = None

    try:
        from discord.ui import MediaGallery, MediaGalleryItem
        HAS_MEDIAGALLERY = True
    except ImportError:
        MediaGallery = None
        MediaGalleryItem = None


# ============================================================
# FARBEN
# ============================================================
NAMED_COLORS = {
    "red": 0xED4245, "green": 0x57F287, "blue": 0x3498DB, "yellow": 0xFEE75C,
    "orange": 0xE67E22, "purple": 0x9B59B6, "pink": 0xEB459E, "magenta": 0xEB459E,
    "gold": 0xF1C40F, "teal": 0x1ABC9C, "blurple": 0x5865F2, "greyple": 0x99AAB5,
    "dark_blue": 0x206694, "dark_red": 0x992D22, "dark_green": 0x1F8B4C,
    "dark_gold": 0xC27C0E, "dark_teal": 0x11806A, "dark_purple": 0x71368A,
    "dark_orange": 0xA84300, "dark_magenta": 0xAD1457, "black": 0x010101,
    "white": 0xFFFFFF, "gray": 0x95A5A6, "grey": 0x95A5A6,
}


def parse_color(s):
    if not s:
        return None
    s = s.strip()
    if s.lower() in NAMED_COLORS:
        return discord.Color(NAMED_COLORS[s.lower()])
    m = re.match(r"^#?([0-9a-fA-F]{6})$", s)
    if m:
        return discord.Color(int(m.group(1), 16))
    return None


def parse_emoji(s):
    if not s:
        return None
    try:
        return discord.PartialEmoji.from_str(s.strip())
    except Exception:
        return None


TYPE_ICONS = {
    "text": "📝", "separator": "➖", "container": "📦",
    "section": "📄", "actionrow": "🔘", "button": "🔵",
    "thumbnail": "🖼️", "mediagallery": "🖼️", "media_item": "🏞️",
}
TYPE_NAMES = {
    "text": "Text", "separator": "Trennlinie", "container": "Container",
    "section": "Section", "actionrow": "Action Row", "button": "Button",
    "thumbnail": "Thumbnail", "mediagallery": "Media Gallery",
    "media_item": "Media Item",
}

# ============================================================
# ERLAUBTE PARENT-TYPEN
# ============================================================
PARENT_ALLOWED = {
    "text": [None, "container", "section"],
    "separator": [None, "container"],
    "container": [None],
    "actionrow": [None, "container"],
    "button": ["actionrow", "section"],
    "media_item": ["mediagallery"],
}

if HAS_SECTION:
    PARENT_ALLOWED["section"] = [None, "container"]
    PARENT_ALLOWED["thumbnail"] = ["section"]

if HAS_MEDIAGALLERY:
    PARENT_ALLOWED["mediagallery"] = [None, "container"]


# ============================================================
# RENDER
# ============================================================
def render_component(cid, state, cog, for_send=False, msg_uid=None):
    comp = state["components"][cid]
    t = comp["type"]
    p = comp.get("props", {})

    if t == "text":
        return TextDisplay(p.get("content") or " ")

    if t == "separator":
        spacing_map = {
            "small": discord.SeparatorSpacingSize.small,
            "large": discord.SeparatorSpacingSize.large,
        }
        return Separator(
            divider=p.get("divider", True),
            spacing=spacing_map.get(p.get("spacing", "small"), discord.SeparatorSpacingSize.small),
        )

    if t == "container":
        c = Container(accent_color=parse_color(p.get("color")) or discord.Color.dark_blue())
        for child_id in comp.get("children", []):
            try:
                item = render_component(child_id, state, cog, for_send, msg_uid)
                if item is not None:
                    c.add_item(item)
            except Exception as e:
                log.error(f"[V2Builder] Container-Child {child_id} fehlgeschlagen: {e}")
        return c

    if t == "section":
        # Fallback: keine Section verfügbar
        if not HAS_SECTION:
            texts = []
            for child_id in comp.get("children", []):
                ch = state["components"].get(child_id)
                if ch and ch["type"] == "text":
                    texts.append(ch["props"].get("content", ""))
            return TextDisplay("\n".join(texts) or " ")

        texts = []
        for child_id in comp.get("children", []):
            ch = state["components"].get(child_id)
            if ch and ch["type"] == "text":
                texts.append(TextDisplay(ch["props"].get("content") or " "))
        if not texts:
            texts = [TextDisplay(" ")]
        texts = texts[:3]

        acc_id = p.get("accessory")
        accessory = None
        if acc_id and acc_id in state["components"]:
            acc = state["components"][acc_id]
            if acc["type"] == "button":
                accessory = render_button(acc_id, state, cog, for_send, msg_uid)
            elif acc["type"] == "thumbnail" and HAS_THUMBNAIL:
                accessory = Thumbnail(acc["props"].get("url") or "https://cdn.discordapp.com/embed/avatars/0.png")

        if accessory is None and HAS_THUMBNAIL:
            accessory = Thumbnail("https://cdn.discordapp.com/embed/avatars/0.png")
        if accessory is None:
            return TextDisplay(" / ".join([tx.content for tx in texts]))

        try:
            return Section(*texts, accessory=accessory)
        except Exception as e:
            log.error(f"[V2Builder] Section-Render fehlgeschlagen: {e}")
            return TextDisplay(" / ".join([tx.content for tx in texts]))

    if t == "actionrow":
        row = ActionRow()
        count = 0
        for child_id in comp.get("children", []):
            if count >= 5:
                break
            ch = state["components"].get(child_id)
            if ch and ch["type"] == "button":
                row.add_item(render_button(child_id, state, cog, for_send, msg_uid))
                count += 1
        if count == 0:
            row.add_item(Button(
                label="Leer", style=discord.ButtonStyle.secondary,
                custom_id=f"v2b_empty_{uuid.uuid4().hex[:8]}", disabled=True
            ))
        return row

    if t == "mediagallery":
        if not HAS_MEDIAGALLERY:
            lines = []
            for child_id in comp.get("children", []):
                ch = state["components"].get(child_id)
                if ch and ch["type"] == "media_item":
                    url = ch["props"].get("url", "")
                    if url:
                        lines.append(url)
            return TextDisplay("\n".join(lines) or " ")

        mg = MediaGallery()
        for child_id in comp.get("children", []):
            ch = state["components"].get(child_id)
            if ch and ch["type"] == "media_item":
                url = ch["props"].get("url")
                if url:
                    mg.add_item(MediaGalleryItem(url, description=ch["props"].get("description") or None))
        return mg

    return TextDisplay("Unbekannter Typ")


def render_button(cid, state, cog, for_send=False, msg_uid=None):
    comp = state["components"][cid]
    p = comp.get("props", {})
    style_map = {
        "primary": discord.ButtonStyle.primary,
        "secondary": discord.ButtonStyle.secondary,
        "success": discord.ButtonStyle.success,
        "danger": discord.ButtonStyle.danger,
        "link": discord.ButtonStyle.link,
    }
    style = style_map.get(p.get("style", "primary"), discord.ButtonStyle.primary)
    emoji = parse_emoji(p.get("emoji"))
    label = p.get("label") or None
    disabled = p.get("disabled", False)

    if style == discord.ButtonStyle.link:
        return Button(
            label=label, style=discord.ButtonStyle.link,
            url=p.get("url") or p.get("action_data") or "https://discord.com",
            emoji=emoji, disabled=disabled,
        )

    if for_send:
        custom_id = f"v2b_{msg_uid}_{cid}"
    else:
        custom_id = f"v2b_preview_{cid}"

    return Button(label=label, style=style, custom_id=custom_id, emoji=emoji, disabled=disabled)


def render_view(state, cog, for_send=False, msg_uid=None):
    view = LayoutView()
    for cid in state.get("root", []):
        try:
            item = render_component(cid, state, cog, for_send, msg_uid)
            if item is not None:
                view.add_item(item)
        except Exception as e:
            log.error(f"[V2Builder] Root-Child {cid} fehlgeschlagen: {e}")
    if not view.children:
        empty = Container(accent_color=discord.Color.dark_gray())
        empty.add_item(TextDisplay("*(Leere Nachricht — füge Komponenten hinzu)*"))
        view.add_item(empty)
    return view


# ============================================================
# STATE MANAGEMENT
# ============================================================
def new_state(user_id):
    return {
        "user_id": user_id,
        "components": {},
        "root": [],
        "cwd": None,
    }


def add_component(state, ctype, parent_id=None, props=None):
    cid = uuid.uuid4().hex[:8]
    state["components"][cid] = {
        "type": ctype,
        "props": props or {},
        "children": [],
    }
    if parent_id and parent_id in state["components"]:
        state["components"][parent_id]["children"].append(cid)
    else:
        state["root"].append(cid)
    return cid


def find_parent(state, cid):
    for pid, comp in state["components"].items():
        if cid in comp.get("children", []):
            return pid
    if cid in state["root"]:
        return None
    return None


def get_siblings(state, parent_id):
    if parent_id is None:
        return state["root"]
    return state["components"][parent_id].get("children", [])


def remove_component(state, cid):
    def _rm(c):
        for child in list(state["components"].get(c, {}).get("children", [])):
            _rm(child)
        state["components"].pop(c, None)

    parent = find_parent(state, cid)
    siblings = get_siblings(state, parent)
    if cid in siblings:
        siblings.remove(cid)
    _rm(cid)


def build_tree_options(state, parent_id):
    result = []

    def _walk(cid, depth):
        comp = state["components"].get(cid)
        if not comp:
            return
        indent = "  " * depth
        icon = TYPE_ICONS.get(comp["type"], "❔")
        p = comp.get("props", {})
        extra = ""
        if comp["type"] == "text":
            txt = (p.get("content") or "").replace("\n", " ")[:30]
            extra = f" — {txt}" if txt else ""
        elif comp["type"] == "button":
            extra = f" — {p.get('label') or 'ohne Label'}"
        elif comp["type"] == "container":
            extra = f" — {len(comp.get('children', []))} Kinder"
        result.append((f"{indent}{icon} {TYPE_NAMES.get(comp['type'], comp['type'])}{extra}", cid))
        for ch in comp.get("children", []):
            _walk(ch, depth + 1)

    if parent_id is None:
        for cid in state["root"]:
            _walk(cid, 0)
    else:
        for cid in state["components"].get(parent_id, {}).get("children", []):
            _walk(cid, 0)
    return result


# ============================================================
# MODALS
# ============================================================
class TextModal(Modal, title="Text bearbeiten"):
    def __init__(self, cog, state, cid, existing=None):
        super().__init__()
        self.cog = cog
        self.state = state
        self.cid = cid
        existing = existing or {}
        self.input = TextInput(
            label="Inhalt",
            placeholder="Markdown erlaubt: **fett**, *kursiv*, `code`, ## Titel",
            default=existing.get("content", ""),
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=4000,
        )
        self.add_item(self.input)

    async def on_submit(self, interaction):
        self.state["components"][self.cid]["props"]["content"] = self.input.value
        await interaction.response.send_message("✅ Text gespeichert.", ephemeral=True)
        await self.cog.refresh_panel(interaction, self.state)


class SeparatorModal(Modal, title="Trennlinie"):
    def __init__(self, cog, state, cid, existing=None):
        super().__init__()
        self.cog = cog
        self.state = state
        self.cid = cid
        existing = existing or {}
        self.divider = TextInput(
            label="Linie anzeigen? (ja/nein)",
            default="ja" if existing.get("divider", True) else "nein",
            required=False, max_length=5,
        )
        self.spacing = TextInput(
            label="Abstand (small/large)",
            default=existing.get("spacing", "small"),
            required=False, max_length=10,
        )
        self.add_item(self.divider)
        self.add_item(self.spacing)

    async def on_submit(self, interaction):
        d = self.divider.value.strip().lower() in ("ja", "yes", "y", "j", "true", "1")
        s = self.spacing.value.strip().lower()
        if s not in ("small", "large"):
            s = "small"
        self.state["components"][self.cid]["props"] = {"divider": d, "spacing": s}
        await interaction.response.send_message("✅ Trennlinie gespeichert.", ephemeral=True)
        await self.cog.refresh_panel(interaction, self.state)


class ContainerModal(Modal, title="Container bearbeiten"):
    def __init__(self, cog, state, cid, existing=None):
        super().__init__()
        self.cog = cog
        self.state = state
        self.cid = cid
        existing = existing or {}
        self.color = TextInput(
            label="Akzentfarbe (Hex oder Name)",
            placeholder="#5865F2 oder dark_blue, red, gold ...",
            default=existing.get("color", ""),
            required=False, max_length=20,
        )
        self.add_item(self.color)

    async def on_submit(self, interaction):
        c = self.color.value.strip()
        if c and not parse_color(c):
            return await interaction.response.send_message(
                f"❌ Farbe `{c}` nicht erkannt.", ephemeral=True
            )
        self.state["components"][self.cid]["props"]["color"] = c
        await interaction.response.send_message("✅ Container gespeichert.", ephemeral=True)
        await self.cog.refresh_panel(interaction, self.state)


class ButtonModal(Modal, title="Button bearbeiten"):
    def __init__(self, cog, state, cid, existing=None):
        super().__init__()
        self.cog = cog
        self.state = state
        self.cid = cid
        existing = existing or {}

        self.label = TextInput(
            label="Label",
            placeholder="z.B. Rolle erhalten",
            default=existing.get("label", ""),
            required=False, max_length=80,
        )
        self.emoji = TextInput(
            label="Emoji (optional)",
            placeholder="z.B. ✅ oder <:name:1234>",
            default=existing.get("emoji", ""),
            required=False, max_length=60,
        )
        self.style = TextInput(
            label="Style (primary/secondary/success/danger/link)",
            default=existing.get("style", "primary"),
            required=False, max_length=20,
        )
        self.action = TextInput(
            label="Aktion (none/role_add/role_remove/role_toggle/say/link)",
            default=existing.get("action", "none"),
            required=False, max_length=20,
        )
        self.action_data = TextInput(
            label="Aktionsdaten (Rollen-ID / Text / URL)",
            placeholder="Rollen-ID (123456...) / Text / https://...",
            default=existing.get("action_data", ""),
            required=False, max_length=500,
        )
        self.add_item(self.label)
        self.add_item(self.emoji)
        self.add_item(self.style)
        self.add_item(self.action)
        self.add_item(self.action_data)

    async def on_submit(self, interaction):
        style = self.style.value.strip().lower()
        if style not in ("primary", "secondary", "success", "danger", "link"):
            style = "primary"

        action = self.action.value.strip().lower()
        if action not in ("none", "role_add", "role_remove", "role_toggle", "say", "link"):
            action = "none"

        if action == "link":
            style = "link"

        self.state["components"][self.cid]["props"] = {
            "label": self.label.value.strip(),
            "emoji": self.emoji.value.strip(),
            "style": style,
            "action": action,
            "action_data": self.action_data.value.strip(),
            "disabled": False,
        }
        await interaction.response.send_message("✅ Button gespeichert.", ephemeral=True)
        await self.cog.refresh_panel(interaction, self.state)


class ThumbnailModal(Modal, title="Thumbnail bearbeiten"):
    def __init__(self, cog, state, cid, existing=None):
        super().__init__()
        self.cog = cog
        self.state = state
        self.cid = cid
        existing = existing or {}
        self.url = TextInput(
            label="Bild-URL",
            placeholder="https://...",
            default=existing.get("url", ""),
            required=True, max_length=500,
        )
        self.add_item(self.url)

    async def on_submit(self, interaction):
        self.state["components"][self.cid]["props"]["url"] = self.url.value.strip()
        await interaction.response.send_message("✅ Thumbnail gespeichert.", ephemeral=True)
        await self.cog.refresh_panel(interaction, self.state)


class MediaItemModal(Modal, title="Media Item bearbeiten"):
    def __init__(self, cog, state, cid, existing=None):
        super().__init__()
        self.cog = cog
        self.state = state
        self.cid = cid
        existing = existing or {}
        self.url = TextInput(
            label="Bild-URL",
            placeholder="https://...",
            default=existing.get("url", ""),
            required=True, max_length=500,
        )
        self.desc = TextInput(
            label="Beschreibung (optional)",
            default=existing.get("description", ""),
            required=False, max_length=200,
        )
        self.add_item(self.url)
        self.add_item(self.desc)

    async def on_submit(self, interaction):
        self.state["components"][self.cid]["props"] = {
            "url": self.url.value.strip(),
            "description": self.desc.value.strip(),
        }
        await interaction.response.send_message("✅ Media Item gespeichert.", ephemeral=True)
        await self.cog.refresh_panel(interaction, self.state)


# ============================================================
# PICKER / ADD VIEWS (klassisch, mit Select)
# ============================================================
class ComponentPickerView(discord.ui.View):
    def __init__(self, cog, state, action, options):
        super().__init__(timeout=120)
        self.cog = cog
        self.state = state
        self.action = action
        select = discord.ui.Select(placeholder="Komponente wählen...", options=options)
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction):
        cid = interaction.data["values"][0]
        action = self.action

        if action == "edit":
            comp = self.state["components"].get(cid)
            if not comp:
                return await interaction.response.send_message("❌ Nicht gefunden.", ephemeral=True)
            return await self.cog.open_edit_modal(interaction, self.state, cid, comp)

        if action == "delete":
            remove_component(self.state, cid)
            await interaction.response.send_message("🗑️ Gelöscht.", ephemeral=True)
            return await self.cog.refresh_panel(interaction, self.state)

        if action in ("up", "down"):
            parent = find_parent(self.state, cid)
            siblings = get_siblings(self.state, parent)
            if cid not in siblings:
                return await interaction.response.send_message("❌ Fehler.", ephemeral=True)
            i = siblings.index(cid)
            if action == "up" and i > 0:
                siblings[i], siblings[i - 1] = siblings[i - 1], siblings[i]
            elif action == "down" and i < len(siblings) - 1:
                siblings[i], siblings[i + 1] = siblings[i + 1], siblings[i]
            await interaction.response.send_message("✅ Verschoben.", ephemeral=True)
            return await self.cog.refresh_panel(interaction, self.state)

        if action == "open":
            comp = self.state["components"].get(cid)
            if not comp:
                return await interaction.response.send_message("❌ Nicht gefunden.", ephemeral=True)
            if comp["type"] not in ("container", "section", "actionrow"):
                return await interaction.response.send_message(
                    "❌ In diesen Typ kann man nicht hineingehen.", ephemeral=True
                )
            self.state["cwd"] = cid
            await interaction.response.send_message("📂 Geöffnet.", ephemeral=True)
            return await self.cog.refresh_panel(interaction, self.state)

        if action == "duplicate":
            comp = self.state["components"].get(cid)
            if not comp:
                return await interaction.response.send_message("❌ Nicht gefunden.", ephemeral=True)
            new_id = uuid.uuid4().hex[:8]
            self.state["components"][new_id] = copy.deepcopy(comp)
            new_children = []
            for ch in comp.get("children", []):
                ch_new = uuid.uuid4().hex[:8]
                self.state["components"][ch_new] = copy.deepcopy(self.state["components"][ch])
                new_children.append(ch_new)
            self.state["components"][new_id]["children"] = new_children
            parent = find_parent(self.state, cid)
            siblings = get_siblings(self.state, parent)
            if cid in siblings:
                idx = siblings.index(cid)
                siblings.insert(idx + 1, new_id)
            await interaction.response.send_message("📋 Dupliziert.", ephemeral=True)
            return await self.cog.refresh_panel(interaction, self.state)


class AddTypeView(discord.ui.View):
    def __init__(self, cog, state, parent_id):
        super().__init__(timeout=120)
        self.cog = cog
        self.state = state
        self.parent_id = parent_id

        parent_type = None
        if parent_id:
            parent_type = state["components"].get(parent_id, {}).get("type")

        all_types = list(PARENT_ALLOWED.keys())
        if not HAS_SECTION:
            all_types = [t for t in all_types if t not in ("section", "thumbnail")]
        if not HAS_MEDIAGALLERY:
            all_types = [t for t in all_types if t not in ("mediagallery", "media_item")]

        types = [t for t in all_types if parent_type in PARENT_ALLOWED[t]]

        options = [
            discord.SelectOption(
                label=TYPE_NAMES[t],
                value=t,
                emoji=TYPE_ICONS.get(t, "❔"),
            )
            for t in types[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="Keine Typen erlaubt", value="__none__")]

        select = discord.ui.Select(placeholder="Typ wählen...", options=options)
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction):
        t = interaction.data["values"][0]
        if t == "__none__":
            return await interaction.response.send_message("❌ Keine Typen erlaubt hier.", ephemeral=True)

        if t == "section" and not HAS_SECTION:
            return await interaction.response.send_message("❌ Section wird nicht unterstützt.", ephemeral=True)
        if t == "thumbnail" and not HAS_THUMBNAIL:
            return await interaction.response.send_message("❌ Thumbnail wird nicht unterstützt.", ephemeral=True)
        if t == "mediagallery" and not HAS_MEDIAGALLERY:
            return await interaction.response.send_message("❌ MediaGallery wird nicht unterstützt.", ephemeral=True)

        cid = add_component(self.state, t, parent_id=self.parent_id)

        defaults = {
            "text": {"content": "Neuer Text"},
            "container": {"color": "dark_blue"},
            "separator": {"divider": True, "spacing": "small"},
            "button": {"label": "Button", "style": "primary", "action": "none", "disabled": False},
            "thumbnail": {"url": "https://cdn.discordapp.com/embed/avatars/0.png"},
            "media_item": {"url": "https://cdn.discordapp.com/embed/avatars/0.png", "description": ""},
        }
        if t in defaults:
            self.state["components"][cid]["props"] = defaults[t]

        await interaction.response.send_message(f"✅ {TYPE_NAMES[t]} hinzugefügt.", ephemeral=True)
        await self.cog.refresh_panel(interaction, self.state)


# ============================================================
# BUILDER-VIEW
# ============================================================
if V2_AVAILABLE:

    class BuilderView(LayoutView):
        def __init__(self, cog, state):
            super().__init__(timeout=600)
            self.cog = cog
            self.state = state

            # Preview
            try:
                preview = render_view(state, cog, for_send=False)
                for child in preview.children:
                    self.add_item(child)
            except Exception as e:
                log.error(f"[V2Builder] Preview-Render-Fehler: {e}")
                c = Container(accent_color=discord.Color.red())
                c.add_item(TextDisplay("Fehler beim Rendern der Vorschau."))
                self.add_item(c)

            # Info
            cwd_text = "📍 **Root**" if state["cwd"] is None else f"📂 **In: {TYPE_NAMES.get(state['components'][state['cwd']]['type'], '?')}**"
            info = Container(accent_color=discord.Color.dark_gray())
            info.add_item(TextDisplay(f"-# {cwd_text} • {len(state['components'])} Komponenten"))
            self.add_item(info)

            # Reihe 1
            row1 = ActionRow()
            row1.add_item(BtnAdd())
            row1.add_item(BtnEdit())
            row1.add_item(BtnDelete())
            row1.add_item(BtnUp())
            row1.add_item(BtnDown())
            self.add_item(row1)

            # Reihe 2
            row2 = ActionRow()
            if state["cwd"] is not None:
                row2.add_item(BtnBack())
            row2.add_item(BtnOpen())
            row2.add_item(BtnSend())
            row2.add_item(BtnClear())
            row2.add_item(BtnClose())
            self.add_item(row2)


    class BtnAdd(Button):
        def __init__(self):
            super().__init__(label="Hinzufügen", style=discord.ButtonStyle.success, emoji="➕", row=0)

        async def callback(self, interaction):
            state = self.view.state
            cwd = state["cwd"]
            if cwd:
                ctype = state["components"][cwd]["type"]
                allowed_types = [t for t, a in PARENT_ALLOWED.items() if ctype in a]
                if not allowed_types:
                    state["cwd"] = None
            view = AddTypeView(self.view.cog, state, state["cwd"])
            await interaction.response.send_message("Was möchtest du hinzufügen?", view=view, ephemeral=True)


    class BtnEdit(Button):
        def __init__(self):
            super().__init__(label="Bearbeiten", style=discord.ButtonStyle.primary, emoji="✏️", row=0)

        async def callback(self, interaction):
            state = self.view.state
            tree = build_tree_options(state, state["cwd"])
            if not tree:
                return await interaction.response.send_message("❌ Keine Komponenten auf dieser Ebene.", ephemeral=True)
            options = [discord.SelectOption(label=label[:100], value=cid) for label, cid in tree[:25]]
            view = ComponentPickerView(self.view.cog, state, "edit", options)
            await interaction.response.send_message("Welche Komponente bearbeiten?", view=view, ephemeral=True)


    class BtnDelete(Button):
        def __init__(self):
            super().__init__(label="Löschen", style=discord.ButtonStyle.danger, emoji="🗑️", row=0)

        async def callback(self, interaction):
            state = self.view.state
            tree = build_tree_options(state, state["cwd"])
            if not tree:
                return await interaction.response.send_message("❌ Keine Komponenten auf dieser Ebene.", ephemeral=True)
            options = [discord.SelectOption(label=label[:100], value=cid) for label, cid in tree[:25]]
            view = ComponentPickerView(self.view.cog, state, "delete", options)
            await interaction.response.send_message("Welche Komponente löschen?", view=view, ephemeral=True)


    class BtnUp(Button):
        def __init__(self):
            super().__init__(label="Hoch", style=discord.ButtonStyle.secondary, emoji="⬆️", row=0)

        async def callback(self, interaction):
            state = self.view.state
            tree = build_tree_options(state, state["cwd"])
            if not tree:
                return await interaction.response.send_message("❌ Keine Komponenten.", ephemeral=True)
            options = [discord.SelectOption(label=label[:100], value=cid) for label, cid in tree[:25]]
            view = ComponentPickerView(self.view.cog, state, "up", options)
            await interaction.response.send_message("Welche Komponente hoch?", view=view, ephemeral=True)


    class BtnDown(Button):
        def __init__(self):
            super().__init__(label="Runter", style=discord.ButtonStyle.secondary, emoji="⬇️", row=0)

        async def callback(self, interaction):
            state = self.view.state
            tree = build_tree_options(state, state["cwd"])
            if not tree:
                return await interaction.response.send_message("❌ Keine Komponenten.", ephemeral=True)
            options = [discord.SelectOption(label=label[:100], value=cid) for label, cid in tree[:25]]
            view = ComponentPickerView(self.view.cog, state, "down", options)
            await interaction.response.send_message("Welche Komponente runter?", view=view, ephemeral=True)


    class BtnOpen(Button):
        def __init__(self):
            super().__init__(label="Öffnen", style=discord.ButtonStyle.secondary, emoji="📂", row=1)

        async def callback(self, interaction):
            state = self.view.state
            tree = build_tree_options(state, state["cwd"])
            filtered = [(l, c) for l, c in tree if state["components"][c]["type"] in ("container", "section", "actionrow")]
            if not filtered:
                return await interaction.response.send_message(
                    "❌ Kein Container/Section/ActionRow auf dieser Ebene.", ephemeral=True
                )
            options = [discord.SelectOption(label=label[:100], value=cid) for label, cid in filtered[:25]]
            view = ComponentPickerView(self.view.cog, state, "open", options)
            await interaction.response.send_message("In welches Element hinein?", view=view, ephemeral=True)


    class BtnBack(Button):
        def __init__(self):
            super().__init__(label="Zurück", style=discord.ButtonStyle.secondary, emoji="⬅️", row=1)

        async def callback(self, interaction):
            state = self.view.state
            if state["cwd"] is not None:
                parent = find_parent(state, state["cwd"])
                state["cwd"] = parent
            await interaction.response.send_message("⬅️ Eine Ebene höher.", ephemeral=True)
            await self.view.cog.refresh_panel(interaction, state)


    class BtnSend(Button):
        def __init__(self):
            super().__init__(label="Senden", style=discord.ButtonStyle.blurple, emoji="📤", row=1)

        async def callback(self, interaction):
            state = self.view.state
            if not state["root"]:
                return await interaction.response.send_message("❌ Die Nachricht ist leer.", ephemeral=True)

            await interaction.response.send_message(
                "In welchen Kanal senden? Erwähne den Kanal mit `#` (30 Sekunden Zeit).",
                ephemeral=True
            )

            def check(m):
                return m.author == interaction.user and m.channel == interaction.channel and m.channel_mentions

            try:
                msg = await interaction.client.wait_for("message", check=check, timeout=30.0)
            except Exception:
                return await interaction.followup.send("⏱️ Timeout.", ephemeral=True)

            target = msg.channel_mentions[0]
            try:
                await msg.delete()
            except discord.Forbidden:
                pass

            await self.view.cog.send_built_message(interaction, state, target)


    class BtnClear(Button):
        def __init__(self):
            super().__init__(label="Leeren", style=discord.ButtonStyle.danger, emoji="🧹", row=1)

        async def callback(self, interaction):
            self.view.state["components"] = {}
            self.view.state["root"] = []
            self.view.state["cwd"] = None
            await interaction.response.send_message("🧹 Geleert.", ephemeral=True)
            await self.view.cog.refresh_panel(interaction, self.view.state)


    class BtnClose(Button):
        def __init__(self):
            super().__init__(label="Schließen", style=discord.ButtonStyle.secondary, emoji="✖️", row=1)

        async def callback(self, interaction):
            self.view.stop()
            self.view.cog.close_session(self.view.state["user_id"])
            try:
                await interaction.message.delete()
            except discord.Forbidden:
                await interaction.response.send_message("Panel geschlossen.", ephemeral=True)


# ============================================================
# HAUPT-COG
# ============================================================
class V2Builder(commands.Cog):
    """Vollständiger Discord Components V2 Builder."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x56324255)
        self.config.register_guild(sent_messages={})
        self.sessions = {}

    def close_session(self, user_id):
        self.sessions.pop(user_id, None)

    async def refresh_panel(self, interaction_or_ctx, state):
        return

    async def open_edit_modal(self, interaction, state, cid, comp):
        t = comp["type"]
        existing = comp.get("props", {})
        if t == "text":
            modal = TextModal(self, state, cid, existing)
        elif t == "separator":
            modal = SeparatorModal(self, state, cid, existing)
        elif t == "container":
            modal = ContainerModal(self, state, cid, existing)
        elif t == "button":
            modal = ButtonModal(self, state, cid, existing)
        elif t == "thumbnail":
            modal = ThumbnailModal(self, state, cid, existing)
        elif t == "media_item":
            modal = MediaItemModal(self, state, cid, existing)
        elif t in ("section", "actionrow", "mediagallery"):
            return await interaction.response.send_message(
                f"{TYPE_NAMES.get(t, t)} hat keine direkten Eigenschaften. Bearbeite die Kinder.",
                ephemeral=True
            )
        else:
            return await interaction.response.send_message("❌ Dieser Typ kann nicht bearbeitet werden.", ephemeral=True)
        await interaction.response.send_modal(modal)

    async def send_built_message(self, interaction, state, channel):
        msg_uid = uuid.uuid4().hex[:12]

        btn_actions = {}
        for cid, comp in state["components"].items():
            if comp["type"] == "button":
                p = comp.get("props", {})
                if p.get("style") != "link" and p.get("action", "none") != "none":
                    btn_actions[cid] = {
                        "action": p.get("action", "none"),
                        "action_data": p.get("action_data", ""),
                        "label": p.get("label", ""),
                    }

        view = render_view(state, self, for_send=True, msg_uid=msg_uid)

        try:
            msg = await channel.send(view=view)
        except discord.Forbidden:
            return await interaction.followup.send(f"❌ Keine Berechtigung in {channel.mention}.", ephemeral=True)
        except Exception as e:
            log.error(f"[V2Builder] Senden fehlgeschlagen: {e}")
            return await interaction.followup.send(f"❌ Fehler: {e}", ephemeral=True)

        async with self.config.guild(interaction.guild).sent_messages() as sent:
            sent[msg_uid] = {
                "message_id": msg.id,
                "channel_id": channel.id,
                "buttons": btn_actions,
            }

        await interaction.followup.send(f"✅ Nachricht in {channel.mention} gesendet.", ephemeral=True)

    @commands.Cog.listener()
    async def on_interaction(self, interaction):
        if interaction.type != discord.InteractionType.component:
            return
        if not interaction.guild:
            return
        data = interaction.data or {}
        custom_id = data.get("custom_id", "")
        if not custom_id.startswith("v2b_"):
            return

        parts = custom_id.split("_")
        if len(parts) < 4:
            return
        msg_uid = parts[2]
        btn_id = parts[3]

        sent = await self.config.guild(interaction.guild).sent_messages()
        record = sent.get(msg_uid)
        if not record:
            return
        btn = record.get("buttons", {}).get(btn_id)
        if not btn:
            return

        action = btn.get("action", "none")
        action_data = btn.get("action_data", "")

        if action == "role_add":
            try:
                rid = int(action_data)
                role = interaction.guild.get_role(rid)
                if role:
                    await interaction.user.add_roles(role, reason="V2-Button")
                    await interaction.response.send_message(f"✅ Du hast die Rolle {role.mention} erhalten.", ephemeral=True)
                else:
                    await interaction.response.send_message("❌ Rolle nicht gefunden.", ephemeral=True)
            except Exception as e:
                await interaction.response.send_message(f"❌ Fehler: {e}", ephemeral=True)

        elif action == "role_remove":
            try:
                rid = int(action_data)
                role = interaction.guild.get_role(rid)
                if role:
                    await interaction.user.remove_roles(role, reason="V2-Button")
                    await interaction.response.send_message(f"✅ Rolle {role.mention} entfernt.", ephemeral=True)
            except Exception as e:
                await interaction.response.send_message(f"❌ Fehler: {e}", ephemeral=True)

        elif action == "role_toggle":
            try:
                rid = int(action_data)
                role = interaction.guild.get_role(rid)
                if role:
                    if role in interaction.user.roles:
                        await interaction.user.remove_roles(role, reason="V2-Button Toggle")
                        await interaction.response.send_message(f"➖ Rolle {role.mention} entfernt.", ephemeral=True)
                    else:
                        await interaction.user.add_roles(role, reason="V2-Button Toggle")
                        await interaction.response.send_message(f"➕ Rolle {role.mention} hinzugefügt.", ephemeral=True)
            except Exception as e:
                await interaction.response.send_message(f"❌ Fehler: {e}", ephemeral=True)

        elif action == "say":
            await interaction.response.send_message(action_data or "(Kein Text)", ephemeral=True)

        else:
            await interaction.response.send_message("✅", ephemeral=True)

    @commands.command(name="v2b", aliases=["v2builder", "v2build"])
    @commands.admin_or_permissions(manage_guild=True)
    async def v2b(self, ctx: commands.Context):
        """Öffnet den V2-Komponenten-Builder."""
        if not V2_AVAILABLE:
            return await ctx.send("❌ Deine discord.py-Version unterstützt kein Components V2.")

        state = new_state(ctx.author.id)
        self.sessions[ctx.author.id] = state

        view = BuilderView(self, state)
        await ctx.send(view=view)

    @commands.command(name="v2bclear")
    @commands.admin_or_permissions(manage_guild=True)
    async def v2bclear(self, ctx: commands.Context):
        """Löscht alle gespeicherten Button-Registrierungen dieser Guild."""
        async with self.config.guild(ctx.guild).sent_messages() as sent:
            sent.clear()
        await ctx.send("✅ Alle gesendeten V2-Nachrichten-Einträge gelöscht.")

    @commands.command(name="v2bhelp")
    async def v2bhelp(self, ctx: commands.Context):
        """Hilfe für den V2-Builder."""
        if not V2_AVAILABLE:
            return await ctx.send("❌ Components V2 nicht verfügbar.")

        available = ["📦 Container", "📝 Text", "➖ Separator", "🔘 ActionRow", "🔵 Button"]
        if HAS_SECTION:
            available.append("📄 Section")
        if HAS_THUMBNAIL:
            available.append("🖼️ Thumbnail")
        if HAS_MEDIAGALLERY:
            available.append("🖼️ MediaGallery")

        embed = discord.Embed(title="🧱 V2 Builder", color=discord.Color.dark_blue())
        embed.description = (
            f"**Verfügbare Komponenten:**\n{' • '.join(available)}\n\n"
            "**Button-Aktionen:**\n"
            "`none` – keine Aktion\n"
            "`role_add` – Rolle geben\n"
            "`role_remove` – Rolle entfernen\n"
            "`role_toggle` – Rolle an/aus\n"
            "`say` – Nachricht an User\n"
            "`link` – URL öffnen (Style: link)\n\n"
            "**Button-Styles:** `primary`, `secondary`, `success`, `danger`, `link`"
        )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(V2Builder(bot))
