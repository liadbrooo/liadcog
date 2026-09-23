import discord
import logging
from redbot.core import commands, Config

log = logging.getLogger("red.beta")

try:
    from discord.ui import LayoutView, Container, TextDisplay, Separator, ActionRow, Button, Modal, TextInput, View
    V2_AVAILABLE = True
except ImportError:
    V2_AVAILABLE = False


# ==========================================
# MODAL 1 (Die ersten 3 Fragen)
# ==========================================
class BetaModal1(Modal):
    def __init__(self, cog):
        super().__init__(title="Betabewerbung - Teil 1/2")
        self.cog = cog

        self.fivem_discord = TextInput(
            label="FiveM + Discord Name",
            placeholder="z.B. MaxMustermann | Max#1234",
            required=True,
            max_length=100
        )
        self.ic_name = TextInput(
            label="Wie willst du IC heißen?",
            placeholder="Dein Roleplay Name",
            required=True,
            max_length=100
        )
        self.discord_id = TextInput(
            label="Discord Nutzer-ID",
            placeholder="z.B. 123456789012345678",
            required=True,
            max_length=25
        )

        self.add_item(self.fivem_discord)
        self.add_item(self.ic_name)
        self.add_item(self.discord_id)

    async def on_submit(self, interaction: discord.Interaction):
        # Daten aus Modal 1 sammeln
        data1 = {
            "fivem_discord": self.fivem_discord.value,
            "ic_name": self.ic_name.value,
            "discord_id": self.discord_id.value,
        }

        # Daten zwischenspeichern für Modal 2
        self.cog.pending_applications[interaction.user.id] = data1

        # Ephemere Nachricht mit Button "Weiter zu Teil 2"
        # (Ein Modal darf NICHT direkt aus einem Modal-Submit heraus geöffnet werden!)
        view = View(timeout=300)  # 5 Minuten gültig
        view.add_item(ContinueToModal2Button())

        await interaction.response.send_message(
            "✅ **Teil 1 abgeschlossen!**\nKlicke auf den Button unten, um mit **Teil 2** fortzufahren.",
            view=view,
            ephemeral=True
        )


# ==========================================
# MODAL 2 (Die restlichen 5 Fragen)
# ==========================================
class BetaModal2(Modal):
    def __init__(self, cog, data1):
        super().__init__(title="Betabewerbung - Teil 2/2")
        self.cog = cog
        self.data1 = data1

        self.fraktion = TextInput(
            label="In welcher Fraktion arbeitest du?",
            placeholder="z.B. Polizeidirektion, Fire Dept, GearHead, Arbeitslos...",
            required=True,
            max_length=100
        )
        self.plan_ic = TextInput(
            label="Was ist dein Plan IC?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500
        )
        self.warum_beta = TextInput(
            label="Warum willst du bei uns in die Beta?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500
        )
        self.warum_dich = TextInput(
            label="Warum sollten wir dich nehmen?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500
        )
        self.bugs = TextInput(
            label="Wirst du Bugs ernst nehmen und melden?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=300
        )

        self.add_item(self.fraktion)
        self.add_item(self.plan_ic)
        self.add_item(self.warum_beta)
        self.add_item(self.warum_dich)
        self.add_item(self.bugs)

    async def on_submit(self, interaction: discord.Interaction):
        # Alle Daten zusammenführen
        data = {
            **self.data1,
            "fraktion": self.fraktion.value,
            "plan_ic": self.plan_ic.value,
            "warum_beta": self.warum_beta.value,
            "warum_dich": self.warum_dich.value,
            "bugs": self.bugs.value,
            "user": interaction.user
        }

        # Zwischenspeicher aufräumen
        self.cog.pending_applications.pop(interaction.user.id, None)

        # An den Log-Kanal senden
        await self.cog.send_application_log(interaction.guild, data)

        # User bestätigen
        await interaction.response.send_message(
            "✅ **Vielen Dank für deine Bewerbung!**\nSie wurde erfolgreich eingereicht und wird vom Team geprüft.",
            ephemeral=True
        )


# ==========================================
# BUTTONS
# ==========================================
class ContinueToModal2Button(Button):
    """Öffnet Modal 2 (muss über einen Button erfolgen, nicht direkt aus Modal 1)."""
    def __init__(self):
        super().__init__(
            label="Weiter zu Teil 2",
            style=discord.ButtonStyle.primary,
            custom_id="beta_continue_modal2_v1",
            emoji="➡️"
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Beta")
        if not cog:
            return

        # Daten aus Modal 1 abrufen
        data1 = cog.pending_applications.get(interaction.user.id)
        if not data1:
            return await interaction.response.send_message(
                "❌ Deine vorherigen Antworten sind verloren gegangen. "
                "Bitte starte die Bewerbung erneut.",
                ephemeral=True
            )

        # Modal 2 öffnen (aus einem Button heraus IST das erlaubt!)
        await interaction.response.send_modal(BetaModal2(cog, data1))


class BetaApplyButton(Button):
    def __init__(self):
        super().__init__(
            label="Jetzt bewerben",
            style=discord.ButtonStyle.primary,
            custom_id="beta_apply_button_v1",
            emoji="📝"
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Beta")
        if not cog:
            return

        # Modal 1 starten
        await interaction.response.send_modal(BetaModal1(cog))


# ==========================================
# VIEW
# ==========================================
class BetaApplicationView(LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

        container = Container(accent_color=discord.Color.dark_blue())
        container.add_item(TextDisplay("## Betabewerbung - Kreis Lindenberg"))
        container.add_item(TextDisplay("Hier kannst du dich auf unserem Server ( Kreis Lindenberg ) für die Beta bewerben."))
        container.add_item(Separator())

        container.add_item(TextDisplay("### Informationen"))
        container.add_item(TextDisplay(
            "1. Es kann sein, dass wir uns dazu entscheiden sollten, doch einen „offenen“ Release durchzuführen und keine Beta zu machen.\n"
            "2. Diese Informationen bleiben Teamintern bzw. High-Team intern!\n"
            "3. Der Release bzw. Beta-Release (falls dieser stattfindet, wird im Discord angekündigt)\n"
            "4. Die Antworten auf die Bewerbungen werden etwas Zeit beanspruchen\n\n"
            "Ansonsten wünschen wir weiterhin viel Spaß und Erfolg :)"
        ))
        container.add_item(Separator())
        container.add_item(TextDisplay("-# Klicke unten auf den Button, um das Bewerbungsformular zu öffnen."))

        row = ActionRow()
        row.add_item(BetaApplyButton())
        container.add_item(row)

        self.add_item(container)


# ==========================================
# HAUPT-COG
# ==========================================
class Beta(commands.Cog):
    """Ein Cog für Beta-Bewerbungen mit V2 Components und sequenziellen Modals."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x42455441)

        default_guild = {
            "channel_id": None,
            "message_id": None,
            "log_channel": None,
        }
        self.config.register_guild(**default_guild)

        # Zwischenspeicher für laufende Bewerbungen (User-ID → Daten aus Modal 1)
        self.pending_applications = {}

        if V2_AVAILABLE:
            self.bot.add_view(BetaApplicationView())

    async def send_application_log(self, guild, data):
        """Sendet die fertige Bewerbung in den Log-Kanal."""
        log_channel_id = await self.config.guild(guild).log_channel()
        if not log_channel_id:
            log.warning("[Beta] Kein Log-Kanal konfiguriert. Bewerbung konnte nicht gesendet werden.")
            # Fallback: DM an den Bewerber? Oder ins Nichts?
            return

        log_channel = guild.get_channel(log_channel_id)
        if not log_channel:
            return

        view = LayoutView()
        container = Container(accent_color=discord.Color.green())

        container.add_item(TextDisplay("## 📝 Neue Betabewerbung"))
        container.add_item(TextDisplay(f"**Bewerber:** {data['user'].mention} (`{data['user'].id}`)"))
        container.add_item(Separator())

        container.add_item(TextDisplay(f"**FiveM + Discord Name:**\n{data['fivem_discord']}"))
        container.add_item(TextDisplay(f"**IC Name:**\n{data['ic_name']}"))
        container.add_item(TextDisplay(f"**Discord ID:**\n{data['discord_id']}"))
        container.add_item(TextDisplay(f"**Fraktion:**\n{data['fraktion']}"))
        container.add_item(TextDisplay(f"**Plan IC:**\n{data['plan_ic']}"))
        container.add_item(TextDisplay(f"**Warum Beta:**\n{data['warum_beta']}"))
        container.add_item(TextDisplay(f"**Warum dich:**\n{data['warum_dich']}"))
        container.add_item(TextDisplay(f"**Bugs melden:**\n{data['bugs']}"))

        view.add_item(container)

        try:
            await log_channel.send(view=view)
        except discord.Forbidden:
            log.error(f"[Beta] Keine Berechtigung für Log-Kanal {log_channel_id}.")
        except Exception as e:
            log.error(f"[Beta] Fehler beim Senden der Bewerbung: {e}")

    # ---------------- COMMANDS ----------------

    @commands.command(name="betasetup")
    @commands.admin_or_permissions(manage_guild=True)
    async def betasetup(self, ctx, channel: discord.TextChannel):
        """Richtet die Beta-Bewerbung in einem Kanal ein."""
        if not V2_AVAILABLE:
            return await ctx.send("❌ Deine discord.py Version unterstützt keine Components V2.")

        embed = discord.Embed(
            title="Betabewerbung - Kreis Lindenberg",
            description="Die Bewerbung wird initialisiert...",
            color=discord.Color.dark_blue()
        )
        msg = await channel.send(embed=embed)

        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).message_id.set(msg.id)

        view = BetaApplicationView()
        try:
            await msg.edit(content=None, embed=None, view=view)
        except Exception as e:
            log.error(f"[Beta] Fehler beim Editieren: {e}")
            return await ctx.send(f"❌ Fehler beim Erstellen der Nachricht: {e}")

        await ctx.send(f"✅ Beta-Bewerbung wurde in {channel.mention} eingerichtet.")

    @commands.command(name="betalog")
    @commands.admin_or_permissions(manage_guild=True)
    async def betalog(self, ctx, channel: discord.TextChannel = None):
        """Setzt den Log-Kanal für eingegangene Bewerbungen."""
        if channel is None:
            await self.config.guild(ctx.guild).log_channel.set(None)
            return await ctx.send("✅ Bewerbungs-Log-Kanal entfernt.")
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"✅ Bewerbungen werden jetzt in {channel.mention} gesendet.")

    @commands.command(name="betaupdate")
    @commands.admin_or_permissions(manage_guild=True)
    async def betaupdate(self, ctx):
        """Erzwingt ein manuelles Update der Bewerbungsnachricht."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        message_id = await self.config.guild(ctx.guild).message_id()

        if not channel_id or not message_id:
            return await ctx.send("❌ Die Bewerbung wurde noch nicht eingerichtet.")

        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            return await ctx.send("❌ Der Kanal existiert nicht mehr.")

        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            return await ctx.send("❌ Die Nachricht existiert nicht mehr. Bitte `betasetup` erneut ausführen.")

        view = BetaApplicationView()
        try:
            await message.edit(content=None, embed=None, view=view)
            await ctx.send("✅ Bewerbungs-Nachricht wurde aktualisiert.")
        except Exception as e:
            await ctx.send(f"❌ Fehler beim Aktualisieren: {e}")

    @commands.command(name="betahelp")
    async def betahelp(self, ctx):
        """Zeigt die Befehle für das Beta-Bewerbungssystem an."""
        embed = discord.Embed(title="📝 Beta-Bewerbung — Befehlsübersicht", color=discord.Color.dark_blue())
        embed.add_field(
            name="⚙️ Setup",
            value=(
                "`betasetup #kanal` — Bewerbung einrichten\n"
                "`betalog #kanal` — Log-Kanal für Bewerbungen setzen\n"
                "`betaupdate` — Nachricht manuell aktualisieren"
            ),
            inline=False,
        )
        embed.set_footer(text="User können sich über den Button in der Nachricht bewerben.")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Beta(bot))
