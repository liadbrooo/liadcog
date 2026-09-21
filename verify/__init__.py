import discord
import logging
from redbot.core import commands, Config

log = logging.getLogger("red.verifikation")

# Prüfen, ob Discord Components V2 verfügbar ist
try:
    from discord.ui import LayoutView, Container, TextDisplay, Separator, ActionRow, Button
    V2_AVAILABLE = True
except ImportError:
    V2_AVAILABLE = False


class VerifyButton(Button):
    """Der persistente Button für die Verifikation."""
    def __init__(self):
        # custom_id ist entscheidend für die Persistenz nach einem Neustart!
        super().__init__(
            label="Verifizieren",
            style=discord.ButtonStyle.success,
            custom_id="verify_role_button_v1",
            emoji="🛡️"
        )

    async def callback(self, interaction: discord.Interaction):
        # Cog holen, um an die Config zu kommen
        cog = interaction.client.get_cog("Verifikation")
        if not cog:
            return

        # Rolle aus der Config laden
        role_id = await cog.config.guild(interaction.guild).role_id()
        if not role_id:
            return await interaction.response.send_message("❌ Es ist noch keine Verifikations-Rolle eingerichtet.", ephemeral=True)

        role = interaction.guild.get_role(role_id)
        if not role:
            return await interaction.response.send_message("❌ Die Verifikations-Rolle existiert nicht mehr auf dem Server.", ephemeral=True)

        member = interaction.user

        # Prüfen, ob User die Rolle schon hat
        if role in member.roles:
            return await interaction.response.send_message("✅ Du bist bereits verifiziert!", ephemeral=True)

        # Rolle geben
        try:
            await member.add_roles(role, reason="Verifikation über Button")
            await interaction.response.send_message(f"✅ Du wurdest erfolgreich verifiziert und hast die Rolle **{role.name}** erhalten!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Ich habe keine Berechtigung, dir diese Rolle zu geben. Bitte kontaktiere einen Admin.", ephemeral=True)


class VerificationLayoutView(LayoutView):
    """Die persistente V2-Ansicht für die Verifikation."""
    def __init__(self):
        super().__init__(timeout=None)  # timeout=None ist Pflicht für Persistenz!

        # Container im V2-Stil
        container = Container(accent_color=discord.Color.blue())
        
        # Texte
        container.add_item(TextDisplay("## Verifikation"))
        container.add_item(TextDisplay("Hier kannst du dich verifizieren lassen."))
        container.add_item(Separator())
        container.add_item(TextDisplay("-# Mit freundlichen Grüßen – Dein Kreis Lindenberg Team"))

        # Button in eine ActionRow packen und in den Container schieben
        row = ActionRow()
        row.add_item(VerifyButton())
        container.add_item(row)

        self.add_item(container)


class Verifikation(commands.Cog):
    """Ein Cog für ein Verifikations-System mit persistenten V2-Buttons."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x56455249)  # VERI

        default_guild = {
            "channel_id": None,
            "message_id": None,
            "role_id": None,
        }
        self.config.register_guild(**default_guild)

        # HIER ist der Trick für die Persistenz nach einem Neustart:
        # Der View wird beim Laden des Cogs beim Bot registriert.
        if V2_AVAILABLE:
            self.bot.add_view(VerificationLayoutView())

    @commands.command(name="verifysetup")
    @commands.admin_or_permissions(manage_guild=True)
    async def verifysetup(self, ctx, channel: discord.TextChannel, role: discord.Role):
        """Richtet die Verifikation ein.
        
        Beispiel: verifysetup #verifizierung @Mitglied
        """
        if not V2_AVAILABLE:
            return await ctx.send("❌ Deine discord.py Version unterstützt keine Components V2. Bitte update den Bot.")

        # Erste Nachricht senden
        embed = discord.Embed(
            title="Verifikation",
            description="Die Verifikation wird initialisiert...",
            color=discord.Color.blue()
        )
        msg = await channel.send(embed=embed)

        # Config speichern
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).message_id.set(msg.id)
        await self.config.guild(ctx.guild).role_id.set(role.id)

        # Nachricht mit dem V2-Design überschreiben
        view = VerificationLayoutView()
        try:
            await msg.edit(content=None, embed=None, view=view)
        except Exception as e:
            log.error(f"[Verifikation] Fehler beim Editieren: {e}")
            return await ctx.send(f"❌ Fehler beim Erstellen der Nachricht: {e}")

        await ctx.send(f"✅ Verifikation wurde in {channel.mention} eingerichtet. User erhalten die Rolle {role.mention}.")

    @commands.command(name="verifyrole")
    @commands.admin_or_permissions(manage_guild=True)
    async def verifyrole(self, ctx, role: discord.Role):
        """Ändert die Rolle, die bei der Verifikation vergeben wird."""
        await self.config.guild(ctx.guild).role_id.set(role.id)
        await ctx.send(f"✅ Die Verifikations-Rolle wurde auf {role.mention} geändert.")

    @commands.command(name="verifyupdate")
    @commands.admin_or_permissions(manage_guild=True)
    async def verifyupdate(self, ctx):
        """Erzwingt ein manuelles Update der Verifikations-Nachricht."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        message_id = await self.config.guild(ctx.guild).message_id()

        if not channel_id or not message_id:
            return await ctx.send("❌ Die Verifikation wurde noch nicht eingerichtet. Nutze `verifysetup`.")

        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            return await ctx.send("❌ Der Kanal existiert nicht mehr.")

        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            return await ctx.send("❌ Die Nachricht existiert nicht mehr. Bitte richte die Verifikation neu ein (`verifysetup`).")

        view = VerificationLayoutView()
        try:
            await message.edit(content=None, embed=None, view=view)
            await ctx.send("✅ Verifikations-Nachricht wurde aktualisiert.")
        except Exception as e:
            await ctx.send(f"❌ Fehler beim Aktualisieren: {e}")

    @commands.command(name="verifyhelp")
    async def verifyhelp(self, ctx):
        """Zeigt die Befehle für das Verifikations-System an."""
        embed = discord.Embed(title="🛡️ Verifikation — Befehlsübersicht", color=discord.Color.blue())
        embed.add_field(
            name="⚙️ Setup",
            value=(
                "`verifysetup #kanal @rolle` — Verifikation einrichten\n"
                "`verifyrole @rolle` — Verifikations-Rolle ändern\n"
                "`verifyupdate` — Nachricht manuell aktualisieren"
            ),
            inline=False,
        )
        embed.set_footer(text="User können sich über den Button in der Nachricht verifizieren.")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Verifikation(bot))
