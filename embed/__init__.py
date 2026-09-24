import discord
import uuid
import re
import logging
from redbot.core import commands, Config

log = logging.getLogger("red.v2builder")

# ============================================================
# SICHERE V2-IMPORTS
# ============================================================
V2_AVAILABLE = False
try:
    from discord.ui import LayoutView, Container, TextDisplay, Separator, ActionRow
    V2_AVAILABLE = True
except ImportError:
    pass

try:
    from discord.ui import Section
    HAS_SECTION = True
except ImportError:
    HAS_SECTION = False

try:
    from discord.ui import Thumbnail
    HAS_THUMBNAIL = True
except ImportError:
    HAS_THUMBNAIL = False

try:
    from discord.ui import MediaGallery, MediaGalleryItem
    HAS_MEDIAGALLERY = True
except ImportError:
    HAS_MEDIAGALLERY = False


# ============================================================
# HELFER
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


def make_container(color=None):
    if color is None:
        color = discord.Color.dark_blue()
    try:
        return Container(accent_color=color)
    except TypeError:
        try:
            return Container(colour=color)
        except TypeError:
            return Container()


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
# RENDER — baut V2 Komponenten
# ============================================================
def render_component(cid, state, for_send=False, msg_uid=None):
    comp = state["components"][cid]
    t = comp["type"]
    p = comp.get("props", {})

    if t == "text":
        return TextDisplay(p.get("content") or " ")

    if t == "separator":
        try:
            spacing = {
                "small": discord.SeparatorSpacingSize.small,
                "large": discord.SeparatorSpacingSize.large,
            }.get(p.get("spacing", "small"), discord.SeparatorSpacingSize.small)
        except AttributeError:
            spacing = None
        try:
            if spacing is not None:
                return Separator(divider=p.get("divider", True), spacing=spacing)
            return Separator(divider=p.get("divider", True))
        except Exception:
            return Separator()

    if t == "container":
        c = make_container(parse_color(p.get("color")) or discord.Color.dark_blue())
        for child_id in comp.get("children", []):
            try:
                item = render_component(child_id, state, for_send, msg_uid)
                if item is not None:
                    c.add_item(item)
            except Exception as e:
                log.error(f"[V2Builder] Container-Child {child_id}: {e}")
        return c

    if t == "section":
        if not HAS_SECTION:
            texts = [state["components"][ch]["props"].get("content", "")
                     for ch in comp.get("children", [])
                     if state["components"].get(ch, {}).get("type") == "text"]
            return TextDisplay("\n".join(texts) or " ")
        texts = [TextDisplay(state["components"][ch]["props"].get("content") or " ")
                 for ch in comp.get("children", [])
                 if state["components"].get(ch, {}).get("type") == "text"]
        if not texts:
            texts = [TextDisplay(" ")]
        texts = texts[:3]
        acc_id = p.get("accessory")
        accessory = None
        if acc_id and acc_id in state["components"]:
            acc = state["components"][acc_id]
            if acc["type"] == "button":
                accessory = render_button(acc_id, state, for_send, msg_uid)
            elif acc["type"] == "thumbnail" and HAS_THUMBNAIL:
                accessory = Thumbnail(acc["props"].get("url") or "https://cdn.discordapp.com/embed/avatars/0.png")
        if accessory is None and HAS_THUMBNAIL:
            accessory = Thumbnail("https://cdn.discordapp.com/embed/avatars/0.png")
        if accessory is None:
            return TextDisplay(" / ".join([tx.content for tx in texts]))
        try:
            return Section(*texts, accessory=accessory)
        except Exception as e:
            log.error(f"[V2Builder] Section: {e}")
            return TextDisplay(" / ".join([tx.content for tx in texts]))

    if t == "actionrow":
        row = ActionRow()
        count = 0
        for child_id in comp.get("children", []):
            if count >= 5:
                break
            ch = state["components"].get(child_id)
            if ch and ch["type"] == "button":
                row.add_item(render_button(child_id, state, for_send, msg_uid))
                count += 1
        if count == 0:
            row.add_item(discord.ui.Button(
                label="Leer", style=discord.ButtonStyle.secondary,
                custom_id=f"v2bdummy_{uuid.uuid4().hex[:8]}", disabled=True
            ))
        return row

    if t == "mediagallery":
        if not HAS_MEDIAGALLERY:
            return TextDisplay("(MediaGallery nicht unterstützt)")
        mg = MediaGallery()
        for child_id in comp.get("children", []):
            ch = state["components"].get(child_id)
            if ch and ch["type"] == "media_item":
                url = ch["props"].get("url")
                if url:
                    mg.add_item(MediaGalleryItem(url, description=ch["props"].get("description") or None))
        return mg

    return TextDisplay("Unbekannter Typ")


def render_button(cid, state, for_send=False, msg_uid=None):
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

    if style == discord.ButtonStyle.link:
        return discord.ui.Button(
            label=label, style=discord.ButtonStyle.link,
            url=p.get("url") or p.get("action_data") or "https://discord.com",
            emoji=emoji,
        )

    if for_send:
        custom_id = f"v2bmsg_{msg_uid}_{cid}"
    else:
        custom_id = f"v2bprev_{cid}"

    return discord.ui.Button(
        label=label, style=style, custom_id=custom_id, emoji=emoji
    )


def build_layout_view(state, for_send=False, msg_uid=None):
    """Baut eine LayoutView mit allen Root-Komponenten."""
    if not V2_AVAILABLE:
        return None
    view = LayoutView()
    for cid in state.get("root", []):
        try:
            item = render_component(cid, state, for_send, msg_uid)
            if item is not None:
                view.add_item(item)
        except Exception as e:
            log.error(f"[V2Builder] Root {cid}: {e}")
    return view


# ============================================================
# STATE
# ============================================================
def new_state(user_id):
    state = {
        "user_id": user_id,
        "components": {},
        "root": [],
        "cwd": None,
        "panel_message": None,
    }
    # Willkommens-Container als Startpunkt
    cid = add_component(state, "container", None, {"color": "dark_blue"})
    tid = add_component(state, "text", cid, {"content": "## Willkommen\nNutze die Buttons unten, um diese Nachricht aufzubauen."})
    return state


def add_component(state, ctype, parent_id=None, props=None):
    cid = uuid.uuid4().hex[:8]
    state["components"][cid] = {"type": ctype, "props": props or {}, "children": []}
    if parent_id and parent_id in state["components"]:
        state["components"][parent_id]["children"].append(cid)
    else:
        state["root"].append(cid)
    return cid


def find_parent(state, cid):
    for pid, comp in state["components"].items():
        if cid in comp.get("children", []):
            return pid
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
            txt = (p.get("content") or "").replace("\n", " ")[:40]
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
class TextModal(discord.ui.Modal, title="Text bearbeiten"):
    def __init__(self, cog, state, cid, existing=None):
        super().__init__()
        self.cog = cog
        self.state = state
        self.cid = cid
        existing = existing or {}
        self.input = discord.ui.TextInput(
            label="Inhalt",
            placeholder="Markdown: **fett**, *kursiv*, ## Titel",
            default=existing.get("content", ""),
            style=discord.TextStyle.paragraph,
            required=False, max_length=4000,
        )
        self.add_item(self.input)

    async def on_submit(self, interaction):
        self.state["components"][self.cid]["props"]["content"] = self.input.value
        await interaction.response.defer()
        await self.cog.refresh_panel(self.state)


class SeparatorModal(discord.ui.Modal, title="Trennlinie"):
    def __init__(self, cog, state, cid, existing=None):
        super().__init__()
        self.cog = cog
        self.state = state
        self.cid = cid
        existing = existing or {}
        self.divider = discord.ui.TextInput(
            label="Linie anzeigen? (ja/nein)",
            default="ja" if existing.get("divider", True) else "nein",
            required=False, max_length=5,
        )
        self.spacing = discord.ui.TextInput(
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
        await interaction.response.defer()
        await self.cog.refresh_panel(self.state)


class ContainerModal(discord.ui.Modal, title="Container"):
    def __init__(self, cog, state, cid, existing=None):
        super().__init__()
        self.cog = cog
        self.state = state
        self.cid = cid
        existing = existing or {}
        self.color = discord.ui.TextInput(
            label="Akzentfarbe (Hex oder Name)",
            placeholder="#5865F2 oder dark_blue, red, gold ...",
            default=existing.get("color", ""),
            required=False, max_length=20,
        )
        self.add_item(self.color)

    async def on_submit(self, interaction):
        c = self.color.value.strip()
        if c and not parse_color(c):
            return await interaction.response.send_message(f"❌ Farbe `{c}` nicht erkannt.", ephemeral=True)
        self.state["components"][self.cid]["props"]["color"] = c
        await interaction.response.defer()
        await self.cog.refresh_panel(self.state)


class ButtonModal(discord.ui.Modal, title="Button"):
    def __init__(self, cog, state, cid, existing=None):
        super().__init__()
        self.cog = cog
        self.state = state
        self.cid = cid
        existing = existing or {}
        self.label = discord.ui.TextInput(label="Label", default=existing.get("label", ""),
                                          required=False, max_length=80)
        self.emoji = discord.ui.TextInput(label="Emoji (optional)", default=existing.get("emoji", ""),
                                          required=False, max_length=60)
        self.style = discord.ui.TextInput(label="Style (primary/secondary/success/danger/link)",
                                          default=existing.get("style", "primary"),
                                          required=False, max_length=20)
        self.action = discord.ui.TextInput(label="Aktion (none/role_add/role_remove/role_toggle/say/link)",
                                           default=existing.get("action", "none"),
                                           required=False, max_length=20)
        self.action_data = discord.ui.TextInput(
            label="Aktionsdaten (Rollen-ID / Text / URL)",
            default=existing.get("action_data", ""),
            required=False, max_length=500,
        )
        for it in (self.label, self.emoji, self.style, self.action, self.action_data):
            self.add_item(it)

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
        await interaction.response.defer()
        await self.cog.refresh_panel(self.state)


class ThumbnailModal(discord.ui.Modal, title="Thumbnail"):
    def __init__(self, cog, state, cid, existing=None):
        super().__init__()
        self.cog = cog
        self.state = state
        self.cid = cid
        existing = existing or {}
        self.url = discord.ui.TextInput(label="Bild-URL", default=existing.get("url", ""),
                                        required=True, max_length=500)
        self.add_item(self.url)

    async def on_submit(self, interaction):
        self.state["components"][self.cid]["props"]["url"] = self.url.value.strip()
        await interaction.response.defer()
        await self.cog.refresh_panel(self.state)


class MediaItemModal(discord.ui.Modal, title="Media Item"):
    def __init__(self, cog, state, cid, existing=None):
        super().__init__()
        self.cog = cog
        self.state = state
        self.cid = cid
        existing = existing or {}
        self.url = discord.ui.TextInput(label="Bild-URL", default=existing.get("url", ""),
                                        required=True, max_length=500)
        self.desc = discord.ui.TextInput(label="Beschreibung (optional)",
                                         default=existing.get("description", ""),
                                         required=False, max_length=200)
        self.add_item(self.url)
        self.add_item(self.desc)

    async def on_submit(self, interaction):
        self.state["components"][self.cid]["props"] = {
            "url": self.url.value.strip(),
            "description": self.desc.value.strip(),
        }
        await interaction.response.defer()
        await self.cog.refresh_panel(self.state)


# ============================================================
# PICKER-VIEWS (klassisch)
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
        a = self.action

        if a == "edit":
            comp = self.state["components"].get(cid)
            if not comp:
                return await interaction.response.send_message("❌ Weg.", ephemeral=True)
            return await self.cog.open_edit_modal(interaction, self.state, cid, comp)

        if a == "delete":
            remove_component(self.state, cid)
            await interaction.response.send_message("🗑️ Gelöscht.", ephemeral=True)
            return await self.cog.refresh_panel(self.state)

        if a in ("up", "down"):
            parent = find_parent(self.state, cid)
            sib = get_siblings(self.state, parent)
            if cid not in sib:
                return await interaction.response.send_message("❌ Fehler.", ephemeral=True)
            i = sib.index(cid)
            if a == "up" and i > 0:
                sib[i], sib[i - 1] = sib[i - 1], sib[i]
            elif a == "down" and i < len(sib) - 1:
                sib[i], sib[i + 1] = sib[i + 1], sib[i]
            await interaction.response.send_message("✅ Verschoben.", ephemeral=True)
            return await self.cog.refresh_panel(self.state)

        if a == "open":
            comp = self.state["components"].get(cid)
            if not comp or comp["type"] not in ("container", "section", "actionrow"):
                return await interaction.response.send_message("❌ Nicht möglich.", ephemeral=True)
            self.state["cwd"] = cid
            await interaction.response.send_message("📂 Geöffnet.", ephemeral=True)
            return await self.cog.refresh_panel(self.state)


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
            discord.SelectOption(label=TYPE_NAMES[t], value=t, emoji=TYPE_ICONS.get(t, "❔"))
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
            return await interaction.response.send_message("❌ Keine Typen erlaubt.", ephemeral=True)

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
        await self.cog.refresh_panel(self.state)


# ============================================================
# PANEL: klassische Buttons (kein V2!) für 100% Zuverlässigkeit
# ============================================================
class PanelView(discord.ui.View):
    def __init__(self, cog, state):
        super().__init__(timeout=1800)
        self.cog = cog
        self.state = state

    @discord.ui.button(label="Hinzufügen", style=discord.ButtonStyle.success, emoji="➕", row=0)
    async def btn_add(self, interaction, button):
        state = self.state
        if state["cwd"]:
            ctype = state["components"][state["cwd"]]["type"]
            allowed = [t for t, a in PARENT_ALLOWED.items() if ctype in a]
            if not allowed:
                state["cwd"] = None
        view = AddTypeView(self.cog, state, state["cwd"])
        await interaction.response.send_message("Was hinzufügen?", view=view, ephemeral=True)

    @discord.ui.button(label="Bearbeiten", style=discord.ButtonStyle.primary, emoji="✏️", row=0)
    async def btn_edit(self, interaction, button):
        state = self.state
        tree = build_tree_options(state, state["cwd"])
        if not tree:
            return await interaction.response.send_message("❌ Nichts da.", ephemeral=True)
        opts = [discord.SelectOption(label=l[:100], value=c) for l, c in tree[:25]]
        view = ComponentPickerView(self.cog, state, "edit", opts)
        await interaction.response.send_message("Bearbeiten?", view=view, ephemeral=True)

    @discord.ui.button(label="Löschen", style=discord.ButtonStyle.danger, emoji="🗑️", row=0)
    async def btn_delete(self, interaction, button):
        state = self.state
        tree = build_tree_options(state, state["cwd"])
        if not tree:
            return await interaction.response.send_message("❌ Nichts da.", ephemeral=True)
        opts = [discord.SelectOption(label=l[:100], value=c) for l, c in tree[:25]]
        view = ComponentPickerView(self.cog, state, "delete", opts)
        await interaction.response.send_message("Löschen?", view=view, ephemeral=True)

    @discord.ui.button(label="Hoch", style=discord.ButtonStyle.secondary, emoji="⬆️", row=0)
    async def btn_up(self, interaction, button):
        state = self.state
        tree = build_tree_options(state, state["cwd"])
        if not tree:
            return await interaction.response.send_message("❌ Nichts da.", ephemeral=True)
        opts = [discord.SelectOption(label=l[:100], value=c) for l, c in tree[:25]]
        view = ComponentPickerView(self.cog, state, "up", opts)
        await interaction.response.send_message("Hoch?", view=view, ephemeral=True)

    @discord.ui.button(label="Runter", style=discord.ButtonStyle.secondary, emoji="⬇️", row=0)
    async def btn_down(self, interaction, button):
        state = self.state
        tree = build_tree_options(state, state["cwd"])
        if not tree:
            return await interaction.response.send_message("❌ Nichts da.", ephemeral=True)
        opts = [discord.SelectOption(label=l[:100], value=c) for l, c in tree[:25]]
        view = ComponentPickerView(self.cog, state, "down", opts)
        await interaction.response.send_message("Runter?", view=view, ephemeral=True)

    @discord.ui.button(label="Öffnen", style=discord.ButtonStyle.secondary, emoji="📂", row=1)
    async def btn_open(self, interaction, button):
        state = self.state
        tree = build_tree_options(state, state["cwd"])
        filtered = [(l, c) for l, c in tree
                    if state["components"][c]["type"] in ("container", "section", "actionrow")]
        if not filtered:
            return await interaction.response.send_message(
                "❌ Kein Container/Section/ActionRow hier.", ephemeral=True
            )
        opts = [discord.SelectOption(label=l[:100], value=c) for l, c in filtered[:25]]
        view = ComponentPickerView(self.cog, state, "open", opts)
        await interaction.response.send_message("Hinein?", view=view, ephemeral=True)

    @discord.ui.button(label="Zurück", style=discord.ButtonStyle.secondary, emoji="⬅️", row=1)
    async def btn_back(self, interaction, button):
        state = self.state
        if state["cwd"] is not None:
            state["cwd"] = find_parent(state, state["cwd"])
            await interaction.response.send_message("⬅️ Eine Ebene höher.", ephemeral=True)
            return await self.cog.refresh_panel(state)
        await interaction.response.send_message("⬅️ Schon auf Root-Ebene.", ephemeral=True)

    @discord.ui.button(label="Vorschau", style=discord.ButtonStyle.secondary, emoji="👁️", row=1)
    async def btn_preview(self, interaction, button):
        state = self.state
        view = build_layout_view(state, for_send=False)
        if not view or not view.children:
            return await interaction.response.send_message("❌ Nichts zu zeigen.", ephemeral=True)
        try:
            await interaction.response.send_message("Vorschau (nur für dich):", view=view, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Vorschau-Fehler: {e}", ephemeral=True)

    @discord.ui.button(label="Senden", style=discord.ButtonStyle.blurple, emoji="📤", row=1)
    async def btn_send(self, interaction, button):
        state = self.state
        if not state["root"]:
            return await interaction.response.send_message("❌ Leer.", ephemeral=True)
        await interaction.response.send_message(
            "In welchen Kanal? Erwähne ihn mit `#` (30 Sek.).", ephemeral=True
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

        await self.cog.send_built_message(interaction, state, target)

    @discord.ui.button(label="Leeren", style=discord.ButtonStyle.danger, emoji="🧹", row=1)
    async def btn_clear(self, interaction, button):
        self.state["components"] = {}
        self.state["root"] = []
        self.state["cwd"] = None
        await interaction.response.send_message("🧹 Geleert.", ephemeral=True)
        await self.cog.refresh_panel(self.state)

    @discord.ui.button(label="Schließen", style=discord.ButtonStyle.secondary, emoji="✖️", row=1)
    async def btn_close(self, interaction, button):
        self.cog.close_session(self.state["user_id"])
        try:
            await interaction.message.delete()
        except discord.Forbidden:
            await interaction.response.send_message("Geschlossen.", ephemeral=True)


# ============================================================
# COG
# ============================================================
class V2Builder(commands.Cog):
    """Components V2 Builder."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x56324255)
        self.config.register_guild(sent_messages={})
        self.sessions = {}

    def close_session(self, user_id):
        self.sessions.pop(user_id, None)

    async def refresh_panel(self, state):
        """Aktualisiert das Panel: löscht die alte Nachricht und sendet eine neue."""
        old = state.get("panel_message")
        if old is None:
            log.warning("[V2Builder] Kein panel_message im State.")
            return
        channel = old.channel
        try:
            await old.delete()
        except Exception as e:
            log.error(f"[V2Builder] Panel-Del: {e}")
        try:
            new_view = PanelView(self, state)
            new_panel = await channel.send(view=new_view)
            state["panel_message"] = new_panel
        except Exception as e:
            log.error(f"[V2Builder] Panel-Refresh: {type(e).__name__}: {e}")

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
                f"{TYPE_NAMES.get(t, t)} hat keine direkten Eigenschaften. Öffne es (📂) und bearbeite die Kinder.",
                ephemeral=True
            )
        else:
            return await interaction.response.send_message("❌ Nicht editierbar.", ephemeral=True)
        await interaction.response.send_modal(modal)

    async def send_built_message(self, interaction, state, channel):
        if not V2_AVAILABLE:
            return await interaction.followup.send("❌ V2 nicht verfügbar.", ephemeral=True)
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

        view = build_layout_view(state, for_send=True, msg_uid=msg_uid)
        try:
            msg = await channel.send(view=view)
        except discord.Forbidden:
            return await interaction.followup.send(f"❌ Keine Berechtigung in {channel.mention}.", ephemeral=True)
        except Exception as e:
            log.error(f"[V2Builder] Senden: {e}")
            return await interaction.followup.send(f"❌ Fehler: {e}", ephemeral=True)

        async with self.config.guild(interaction.guild).sent_messages() as sent:
            sent[msg_uid] = {
                "message_id": msg.id,
                "channel_id": channel.id,
                "buttons": btn_actions,
            }

        await interaction.followup.send(f"✅ Gesendet in {channel.mention}.", ephemeral=True)

    @commands.Cog.listener()
    async def on_interaction(self, interaction):
        if interaction.type != discord.InteractionType.component:
            return
        if not interaction.guild:
            return
        data = interaction.data or {}
        custom_id = data.get("custom_id", "")

        # Preview-Buttons: freundliche Antwort statt "Interaction failed"
        if custom_id.startswith("v2bprev_"):
            try:
                await interaction.response.send_message(
                    "👁️ Dies ist nur die Vorschau. Die Aktion wird in der **gesendeten** Nachricht ausgeführt.",
                    ephemeral=True
                )
            except Exception:
                pass
            return

        if not custom_id.startswith("v2bmsg_"):
            return

        parts = custom_id.split("_")
        if len(parts) < 3:
            return
        msg_uid = parts[1]
        btn_id = parts[2]

        sent = await self.config.guild(interaction.guild).sent_messages()
        record = sent.get(msg_uid)
        if not record:
            return
        btn = record.get("buttons", {}).get(btn_id)
        if not btn:
            return

        action = btn.get("action", "none")
        action_data = btn.get("action_data", "")

        if action in ("role_add", "role_remove", "role_toggle"):
            try:
                rid = int(action_data)
            except ValueError:
                return await interaction.response.send_message("❌ Ungültige Rollen-ID.", ephemeral=True)
            role = interaction.guild.get_role(rid)
            if not role:
                return await interaction.response.send_message("❌ Rolle nicht gefunden.", ephemeral=True)
            try:
                if action == "role_add":
                    await interaction.user.add_roles(role, reason="V2-Button")
                    await interaction.response.send_message(f"✅ Rolle {role.mention} erhalten.", ephemeral=True)
                elif action == "role_remove":
                    await interaction.user.remove_roles(role, reason="V2-Button")
                    await interaction.response.send_message(f"✅ Rolle {role.mention} entfernt.", ephemeral=True)
                else:
                    if role in interaction.user.roles:
                        await interaction.user.remove_roles(role, reason="V2-Button Toggle")
                        await interaction.response.send_message(f"➖ {role.mention} entfernt.", ephemeral=True)
                    else:
                        await interaction.user.add_roles(role, reason="V2-Button Toggle")
                        await interaction.response.send_message(f"➕ {role.mention} hinzugefügt.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
        elif action == "say":
            await interaction.response.send_message(action_data or "(Kein Text)", ephemeral=True)
        else:
            await interaction.response.send_message("✅", ephemeral=True)

    @commands.command(name="v2b", aliases=["v2builder", "v2build"])
    @commands.admin_or_permissions(manage_guild=True)
    async def v2b(self, ctx: commands.Context):
        """Öffnet den V2-Builder."""
        if not V2_AVAILABLE:
            return await ctx.send("❌ Components V2 nicht verfügbar.")

        state = new_state(ctx.author.id)
        self.sessions[ctx.author.id] = state

        view = PanelView(self, state)
        panel = await ctx.send(view=view)
        state["panel_message"] = panel

    @commands.command(name="v2bclear")
    @commands.admin_or_permissions(manage_guild=True)
    async def v2bclear(self, ctx: commands.Context):
        async with self.config.guild(ctx.guild).sent_messages() as sent:
            sent.clear()
        await ctx.send("✅ Einträge gelöscht.")

    @commands.command(name="v2bhelp")
    async def v2bhelp(self, ctx: commands.Context):
        if not V2_AVAILABLE:
            return await ctx.send("❌ Components V2 nicht verfügbar.")
        embed = discord.Embed(title="🧱 V2 Builder", color=discord.Color.dark_blue())
        embed.description = (
            "**Buttons:**\n"
            "➕ Hinzufügen — Komponente hinzufügen\n"
            "✏️ Bearbeiten — Eigenschaften ändern\n"
            "🗑️ Löschen — Komponente entfernen\n"
            "⬆️⬇️ Hoch/Runter — Reihenfolge ändern\n"
            "📂 Öffnen — in Container/Section/ActionRow hinein\n"
            "⬅️ Zurück — eine Ebene höher\n"
            "👁️ Vorschau — V2-Vorschau zeigen\n"
            "📤 Senden — in einen Kanal posten\n"
            "🧹 Leeren — alles wegwerfen\n\n"
            "**Button-Aktionen** (im Button-Modal):\n"
            "`none` • `role_add` • `role_remove` • `role_toggle` • `say` • `link`"
        )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(V2Builder(bot))
