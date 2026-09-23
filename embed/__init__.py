import discord
import logging
from redbot.core import commands, Config

log = logging.getLogger("red.embedbuilder")

# V2 Import mit sicherem Fallback
V2_AVAILABLE = False
try:
    from discord.ui import (
        LayoutView, Container, TextDisplay, Separator,
        ActionRow, Button, Modal, TextInput
    )
    V2_AVAILABLE = True
except ImportError:
    from discord.ui import Modal, TextInput, Button


# ============================================================
# MODALS (Eingabe-Fenster)
# ============================================================

class TitleModal(Modal, title="Titel bearbeiten"):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.title_input = TextInput(
            label="Titel",
            placeholder="z.B. Willkommen auf Kreis Lindenberg",
            default=view.data.get("title", ""),
            required=False,
            max_length=256
        )
        self.add_item(self.title_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.view.data["title"] = self.title_input.value
        await self.view.refresh(interaction)


class DescriptionModal(Modal, title="Beschreibung bearbeiten"):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.desc_input = TextInput(
            label="Beschreibung",
            placeholder="Markdown erlaubt (**fett**, *kursiv*)",
            default=view.data.get("description", ""),
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=4000
        )
        self.add_item(self.desc_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.view.data["description"] = self.desc_input.value
        await self.view.refresh(interaction)


class ColorModal(Modal, title="Farbe bearbeiten"):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.color_input = TextInput(
            label="Farbe (Hex oder Name)",
            placeholder="z.B. #5865F2, dark_blue, red, gold",
            default=view.data.get("color", ""),
            required=False,
            max_length=20
        )
        self.add_item(self.color_input)

    async def on_submit(self, interaction: discord.Interaction):
        color_str = self.color_input.value.strip()
        if color_str:
            if not self.view.cog.parse_color(color_str):
                return await interaction.response.send_message(
                    f"⚠️ Farbe `{color_str}` nicht erkannt. Nutze Hex (`#5865F2`) "
                    "oder einen Namen (`red`, `dark_blue`...).",
                    ephemeral=True
                )
        self.view.data["color"] = color_str
        await self.view.refresh(interaction)


class FooterModal(Modal, title="Footer bearbeiten"):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.footer_input = TextInput(
            label="Footer (kleiner Text unten)",
            placeholder="z.B. Kreis Lindenberg",
            default=view.data.get("footer", ""),
            required=False,
            max_length=200
        )
        self.add_item(self.footer_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.view.data["footer"] = self.footer_input.value
        await self.view.refresh(interaction)


class AuthorModal(Modal, title="Autor bearbeiten"):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.author_input = TextInput(
            label="Autor (oben klein)",
            placeholder="z.B. Team Kreis Lindenberg",
            default=view.data.get("author", ""),
            required=False,
            max_length=100
        )
        self.add_item(self.author_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.view.data["author"] = self.author_input.value
        await self.view.refresh(interaction)


class AddFieldModal(Modal, title="Feld hinzufügen"):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.field_name = TextInput(
            label="Feldname",
            placeholder="z.B. Regeln, Adresse, Kontakt",
            required=True,
            max_length=100
        )
        self.field_value = TextInput(
            label="Feldwert",
            placeholder="Der Inhalt des Feldes",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        self.add_item(self.field_name)
        self.add_item(self.field_value)

    async def on_submit(self, interaction: discord.Interaction):
        fields = self.view.data.setdefault("fields", [])
        fields.append({
            "name": self.field_name.value,
            "value": self.field_value.value
        })
        await self.view.refresh(interaction)


class RemoveFieldModal(Modal, title="Feld entfernen"):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.index_input = TextInput(
            label="Feld-Nummer (beginnend bei 1)",
            placeholder="z.B. 1",
            required=True,
            max_length=2
        )
        self.add_item(self.index_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            idx = int(self.index_input.value) - 1
        except ValueError:
            return await interaction.response.send_message("❌ Bitte eine Zahl angeben.", ephemeral=True)

        fields = self.view.data.get("fields", [])
        if idx < 0 or idx >= len(fields):
            return await interaction.response.send_message(
                f"❌ Feld **{idx+1}** existiert nicht ({len(fields)} Felder).", ephemeral=True
            )

        removed = fields.pop(idx)
        await interaction.response.send_message(
            f"✅ Feld **{removed['name']}** wurde entfernt.", ephemeral=True
        )
        await self.view.refresh(interaction, already_responded=True)


# ============================================================
# VIEW (Interaktives Panel)
# ============================================================

if V2_AVAILABLE:

    class BuilderView(LayoutView):
        def __init__(self, cog, data: dict = None):
            super().__init__(timeout=300)
            self.cog = cog
            self.data = data or {
                "title": "",
                "description": "",
                "color": "",
                "footer": "",
                "author": "",
                "fields": [],
            }

        def build_preview_container(self):
            """Baut den Container mit der aktuellen Vorschau."""
            color = self.cog.parse_color(self.data.get("color", "")) or discord.Color.dark_blue()
            container = Container(accent_color=color)

            has_content = False

            if self.data.get("author"):
                container.add_item(TextDisplay(f"-# {self.data['author']}"))
                has_content = True

            if self.data.get("title"):
                container.add_item(TextDisplay(f"## {self.data['title']}"))
                has_content = True

            if self.data.get("description"):
                container.add_item(TextDisplay(self.data["description"]))
                has_content = True

            for i, field in enumerate(self.data.get("fields", []), 1):
                container.add_item(TextDisplay(f"### {i}. {field['name']}\n{field['value']}"))
                has_content = True

            if self.data.get("footer"):
                if has_content:
                    container.add_item(Separator())
                container.add_item(TextDisplay(f"-# {self.data['footer']}"))
                has_content = True

            if not has_content:
                container.add_item(TextDisplay("*(Leeres Embed — nutze die Buttons unten)*"))

            return container

        def build_control_row(self):
            """Baut die ActionRow mit allen Bearbeitungs-Buttons."""
            row1 = ActionRow()
            row1.add_item(BtnTitle())
            row1.add_item(BtnDescription())
            row1.add_item(BtnColor())
            row1.add_item(BtnAuthor())
            row1.add_item(BtnFooter())

            row2 = ActionRow()
            row2.add_item(BtnAddField())
            row2.add_item(BtnRemoveField())
            row2.add_item(BtnClear())
            row2.add_item(BtnSend())
            row2.add_item(BtnCancel())

            return row1, row2

        async def refresh(self, interaction: discord.Interaction, already_responded: bool = False):
            """Baut die View neu auf."""
            new_view = BuilderView(self.cog, self.data)
            try:
                if already_responded:
                    await interaction.edit_original_response(view=new_view)
                else:
                    await interaction.response.edit_message(view=new_view)
            except Exception as e:
                log.error(f"[EmbedBuilder] Fehler beim Refresh: {e}")


    # ---------- BUTTONS ----------

    class BtnTitle(Button):
        def __init__(self):
            super().__init__(label="Titel", style=discord.ButtonStyle.grey, row=0, emoji="📝")

        async def callback(self, interaction: discord.Interaction):
            view = self.view
            await interaction.response.send_modal(TitleModal(view))


    class BtnDescription(Button):
        def __init__(self):
            super().__init__(label="Beschreibung", style=discord.ButtonStyle.grey, row=0, emoji="📄")

        async def callback(self, interaction: discord.Interaction):
            view = self.view
            await interaction.response.send_modal(DescriptionModal(view))


    class BtnColor(Button):
        def __init__(self):
            super().__init__(label="Farbe", style=discord.ButtonStyle.grey, row=0, emoji="🎨")

        async def callback(self, interaction: discord.Interaction):
            view = self.view
            await interaction.response.send_modal(ColorModal(view))


    class BtnAuthor(Button):
        def __init__(self):
            super().__init__(label="Autor", style=discord.ButtonStyle.grey, row=0, emoji="👤")

        async def callback(self, interaction: discord.Interaction):
            view = self.view
            await interaction.response.send_modal(AuthorModal(view))


    class BtnFooter(Button):
        def __init__(self):
            super().__init__(label="Footer", style=discord.ButtonStyle.grey, row=0, emoji="📎")

        async def callback(self, interaction: discord.Interaction):
            view = self.view
            await interaction.response.send_modal(FooterModal(view))


    class BtnAddField(Button):
        def __init__(self):
            super().__init__(label="Feld +", style=discord.ButtonStyle.green, row=1, emoji="➕")

        async def callback(self, interaction: discord.Interaction):
            view = self.view
            await interaction.response.send_modal(AddFieldModal(view))


    class BtnRemoveField(Button):
        def __init__(self):
            super().__init__(label="Feld -", style=discord.ButtonStyle.red, row=1, emoji="➖")

        async def callback(self, interaction: discord.Interaction):
            view = self.view
            if not view.data.get("fields"):
                return await interaction.response.send_message("❌ Keine Felder zum Entfernen.", ephemeral=True)
            await interaction.response.send_modal(RemoveFieldModal(view))


    class BtnClear(Button):
        def __init__(self):
            super().__init__(label="Leeren", style=discord.ButtonStyle.danger, row=1, emoji="🗑️")

        async def callback(self, interaction: discord.Interaction):
            view = self.view
            view.data = {
                "title": "",
                "description": "",
                "color": "",
                "footer": "",
                "author": "",
                "fields": [],
            }
            await view.refresh(interaction)


    class BtnSend(Button):
        def __init__(self):
            super().__init__(label="Senden", style=discord.ButtonStyle.blurple, row=1, emoji="📤")

        async def callback(self, interaction: discord.Interaction):
            view = self.view
            data = view.data

            # Prüfen ob überhaupt was drin ist
            if not any([data.get("title"), data.get("description"), data.get("fields"), data.get("footer"), data.get("author")]):
                return await interaction.response.send_message("❌ Das Embed ist leer.", ephemeral=True)

            # Kanal-Auswahl anbieten
            await interaction.response.send_message(
                "In welchen Kanal soll das Embed gesendet werden? "
                "Erwähne den Kanal mit `#` in diesem Channel (30 Sekunden).",
                ephemeral=True
            )

            def check(m):
                return m.author == interaction.user and m.channel == interaction.channel and m.channel_mentions

            try:
                msg = await interaction.client.wait_for("message", check=check, timeout=30.0)
            except Exception:
                return await interaction.followup.send("⏱️ Timeout. Senden abgebrochen.", ephemeral=True)

            target_channel = msg.channel_mentions[0]
            try:
                await msg.delete()
            except discord.Forbidden:
                pass

            # View bauen und senden
            final_container = view.build_preview_container()
            final_view = LayoutView()
            final_view.add_item(final_container)

            try:
                await target_channel.send(view=final_view)
                await interaction.followup.send(f"✅ Embed wurde in {target_channel.mention} gesendet.", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send(f"❌ Keine Berechtigung für {target_channel.mention}.", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"❌ Fehler: {e}", ephemeral=True)


    class BtnCancel(Button):
        def __init__(self):
            super().__init__(label="Abbrechen", style=discord.ButtonStyle.secondary, row=1, emoji="✖️")

        async def callback(self, interaction: discord.Interaction):
            view = self.view
            view.stop()
            try:
                await interaction.message.delete()
            except discord.Forbidden:
                await interaction.response.send_message("Panel geschlossen.", ephemeral=True)


# ============================================================
# HAUPT-COG
# ============================================================

class EmbedBuilder(commands.Cog):
    """Interaktiver Embed-Builder im Components V2 Format."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x454D4244)  # EMBD
        self.config.register_guild(saved_embeds={})

    # ---------------- HELPER ----------------

    def parse_color(self, color_str: str):
        """Konvertiert Hex oder Farbnamen zu discord.Color."""
        if not color_str:
            return None

        color_str = color_str.strip()
        named = {
            "red": discord.Color.red(),
            "green": discord.Color.green(),
            "blue": discord.Color.blue(),
            "yellow": discord.Color.yellow(),
            "orange": discord.Color.orange(),
            "purple": discord.Color.purple(),
            "pink": discord.Color.magenta(),
            "magenta": discord.Color.magenta(),
            "gold": discord.Color.gold(),
            "teal": discord.Color.teal(),
            "blurple": discord.Color.blurple(),
            "greyple": discord.Color.greyple(),
            "dark_blue": discord.Color.dark_blue(),
            "dark_red": discord.Color.dark_red(),
            "dark_green": discord.Color.dark_green(),
            "dark_gold": discord.Color.dark_gold(),
            "dark_teal": discord.Color.dark_teal(),
            "dark_purple": discord.Color.dark_purple(),
            "dark_orange": discord.Color.dark_orange(),
            "dark_magenta": discord.Color.dark_magenta(),
            "black": discord.Color.from_rgb(0, 0, 0),
            "white": discord.Color.from_rgb(255, 255, 255),
            "gray": discord.Color.from_rgb(128, 128, 128),
            "grey": discord.Color.from_rgb(128, 128, 128),
        }

        key = color_str.lower().replace(" ", "_")
        if key in named:
            return named[key]

        import re
        match = re.match(r"^#?([0-9a-fA-F]{6})$", color_str)
        if match:
            return discord.Color(int(match.group(1), 16))

        return None

    # ---------------- COMMANDS ----------------

    @commands.command(name="embedbuilder", aliases=["eb"])
    @commands.admin_or_permissions(manage_guild=True)
    async def embedbuilder(self, ctx: commands.Context):
        """Öffnet den interaktiven Embed-Builder."""
        if not V2_AVAILABLE:
            return await ctx.send("❌ Deine discord.py-Version unterstützt keine Components V2.")

        view = BuilderView(self)
        await ctx.send(view=view)


async def setup(bot):
    await bot.add_cog(EmbedBuilder(bot))
