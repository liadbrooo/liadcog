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
        super().__init__(
            label="Verifizieren",
            style=discord.ButtonStyle.success,
            custom_id="verify_role_button_v1",  # Wichtig für Persistenz!
            emoji="🛡️"
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Verifikation")
        if not cog:
            return

        role_id = await cog.config.guild(interaction.guild).role_id()
        if not role_id:
            return await interaction.response.send_message(
                "❌ Es ist noch keine Verifikations-Rolle eingerichtet.",
                ephemeral=True
            )

        role = interaction.guild.get_role(role_id)
        if not role:
            return await interaction.response.send_message(
                "❌ Die konfigurierte Verifikations-Rolle existiert nicht mehr auf dem Server. "
                "Bitte einen Admin, `verifysetup` erneut auszuführen.",
                ephemeral=True
            )

        if role.is_default():
            return await interaction.response.send_message(
                "❌ Die Verifikations-Rolle ist auf `@everyone` gesetzt. "
                "Das ist ein Konfigurationsfehler – bitte einen Admin, `verifyrole @NeueRolle` auszuführen.",
                ephemeral=True
            )

        # WICHTIG: Frische Mitglieder-Daten von Discord holen (nicht aus dem Cache)
        try:
            member = await interaction.guild.fetch_member(interaction.user.id)
        except discord.NotFound:
            member = interaction.user

        if role in member.roles:
            return await interaction.response.send_message(
                f"✅ Du bist bereits verifiziert und hast die Rolle **{role.name}**.",
                ephemeral=True
            )

        # Rolle geben
        try:
            await member.add_roles(role, reason="Verifikation über Button")
            await interaction.response.send_message(
                f"✅ Du wurdest erfolgreich verifiziert und hast die Rolle **{role.name}** erhalten!",
                ephemeral=True
            )
            # Log-Nachricht senden
            await cog.send_verification_log(interaction.guild, member, role)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Ich habe keine Berechtigung, dir diese Rolle zu geben. "
                "**Wichtig:** Die Rolle des Bots muss in der Server-Rollenliste **über** der Verifikations-Rolle stehen.",
                ephemeral=True
            )


class VerificationLayoutView(LayoutView):
    """Die persistente V2-Ansicht für die Verifikation."""
    def __init__(self):
        super().__init__(timeout=None)  # timeout=None ist Pflicht für Persistenz!

        # Dark Blue Akzent
        container = Container(accent_color=discord.Color.dark_blue())
        container.add_item(TextDisplay("## Verifikation"))
        container.add_item(TextDisplay("Hier kannst du dich verifizieren lassen."))
        container.add_item(Separator())
        container.add_item(TextDisplay("-# Mit freundlichen Grüßen – Dein Kreis Lindenberg Team"))

        row = ActionRow()
        row.add_item(VerifyButton())
        container.add_item(row)

        self.add_item(container)


class Verifikation(commands.Cog):
    """Ein Cog für ein Verifikations-System mit persistenten V2-Buttons."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x56455249)

        default_guild = {
            "channel_id": None,
            "message_id": None,
            "role_id": None,
            "log_channel": None,  # NEU: Log-Kanal
        }
        self.config.register_guild(**default_guild)

        # HIER: View beim Start registrieren, damit der Button nach Neustart funktioniert
        if V2_AVAILABLE:
            self.bot.add_view(VerificationLayoutView())

    async def send_verification_log(self, guild, member, role):
        """Sendet eine Log-Nachricht in den konfigurierten Log-Kanal."""
        log_channel_id = await self.config.guild(guild).log_channel()
        if not log_channel_id:
            return
        log_channel = guild.get_channel(log_channel_id)
        if not log_channel:
            return
        
        embed = discord.Embed(title="🛡️ Neue Verifikation", color=discord.Color.dark_blue())
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Rolle", value=role.mention, inline=True)
        embed.set_footer(text=f"User-ID: {member.id}")
        embed.timestamp = discord.utils.utcnow()
        
        try:
            await log_channel.send(embed=embed)
        except discord.Forbidden:
            log.warning(f"[Verifikation] Keine Berechtigung für Log-Kanal {log_channel_id}.")

    @commands.command(name="verifysetup")
    @commands.admin_or_permissions(manage_guild=True)
    async def verifysetup(self, ctx, channel: discord.TextChannel, role: discord.Role):
        """Richtet die Verifikation ein.

        Beispiel: verifysetup #verifizierung @Mitglied
        """
        if not V2_AVAILABLE:
            return await ctx.send("❌ Deine discord.py Version unterstützt keine Components V2.")

        if role.is_default():
            return await ctx.send(
                "❌ Du kannst `@everyone` nicht als Verifikations-Rolle verwenden. "
                "Bitte wähle eine normale Rolle, die User **nicht** automatisch haben."
            )

        bot_member = ctx.guild.get_member(self.bot.user.id)
        if bot_member and bot_member.top_role.position <= role.position:
            return await ctx.send(
                f"❌ Meine höchste Rolle (`{bot_member.top_role.name}`) steht **unter** der Verifikations-Rolle "
                f"(`{role.name}`). Ich kann sie nicht vergeben. "
                "Bitte ziehe die Bot-Rolle in den Server-Einstellungen über die Verifikations-Rolle."
            )

        embed = discord.Embed(
            title="Verifikation",
            description="Die Verifikation wird initialisiert...",
            color=discord.Color.dark_blue()
        )
        msg = await channel.send(embed=embed)

        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).message_id.set(msg.id)
        await self.config.guild(ctx.guild).role_id.set(role.id)

        view = VerificationLayoutView()
        try:
            await msg.edit(content=None, embed=None, view=view)
        except Exception as e:
            log.error(f"[Verifikation] Fehler beim Editieren: {e}")
            return await ctx.send(f"❌ Fehler beim Erstellen der Nachricht: {e}")

        await ctx.send(
            f"✅ Verifikation wurde in {channel.mention} eingerichtet.\n"
            f"🎯 Vergebene Rolle: **{role.name}** (ID: `{role.id}`, Position: `{role.position}`)\n"
            f"👉 Stelle sicher, dass diese Rolle **nicht** automatisch an neue User vergeben wird."
        )

    @commands.command(name="verifyrole")
    @commands.admin_or_permissions(manage_guild=True)
    async def verifyrole(self, ctx, role: discord.Role):
        """Ändert die Rolle, die bei der Verifikation vergeben wird."""
        if role.is_default():
            return await ctx.send("❌ `@everyone` kann nicht als Verifikations-Rolle verwendet werden.")
        await self.config.guild(ctx.guild).role_id.set(role.id)
        await ctx.send(f"✅ Die Verifikations-Rolle wurde auf **{role.name}** (`{role.id}`) geändert.")

    @commands.command(name="verifylog")
    @commands.admin_or_permissions(manage_guild=True)
    async def verifylog(self, ctx, channel: discord.TextChannel = None):
        """Setzt einen Log-Kanal für Verifikationen.
        
        Ohne Kanal-Angabe wird der Log-Kanal entfernt.
        """
        if channel is None:
            await self.config.guild(ctx.guild).log_channel.set(None)
            return await ctx.send("✅ Log-Kanal für Verifikationen entfernt.")
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"✅ Verifikations-Log-Kanal auf {channel.mention} gesetzt.")

    @commands.command(name="verifystats")
    @commands.admin_or_permissions(manage_guild=True)
    async def verifystats(self, ctx):
        """Zeigt Statistiken über die Verifikationen an."""
        role_id = await self.config.guild(ctx.guild).role_id()
        if not role_id:
            return await ctx.send("❌ Es ist noch keine Verifikations-Rolle eingerichtet.")

        role = ctx.guild.get_role(role_id)
        if not role:
            return await ctx.send("❌ Die konfigurierte Rolle existiert nicht mehr.")

        # Zähle alle Member mit der Rolle
        verified_count = sum(1 for m in ctx.guild.members if role in m.roles)
        total_members = ctx.guild.member_count
        percentage = (verified_count / total_members) * 100 if total_members > 0 else 0

        embed = discord.Embed(
            title="📊 Verifikations-Statistik",
            color=discord.Color.dark_blue()
        )
        embed.add_field(name="Verifizierte User", value=f"**{verified_count}**", inline=True)
        embed.add_field(name="Gesamte User", value=f"**{total_members}**", inline=True)
        embed.add_field(name="Verifizierungsquote", value=f"**{percentage:.1f}%**", inline=True)
        embed.set_footer(text=f"Rolle: {role.name}")
        await ctx.send(embed=embed)

    @commands.command(name="verifystatus")
    @commands.admin_or_permissions(manage_guild=True)
    async def verifystatus(self, ctx):
        """Zeigt den aktuellen Status der Verifikations-Konfiguration."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        message_id = await self.config.guild(ctx.guild).message_id()
        role_id = await self.config.guild(ctx.guild).role_id()
        log_channel_id = await self.config.guild(ctx.guild).log_channel()

        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        role = ctx.guild.get_role(role_id) if role_id else None
        log_channel = ctx.guild.get_channel(log_channel_id) if log_channel_id else None

        embed = discord.Embed(title="🛡️ Verifikations-Status", color=discord.Color.dark_blue())
        embed.add_field(name="Kanal", value=channel.mention if channel else "❌ Nicht gesetzt", inline=False)
        embed.add_field(name="Nachricht-ID", value=f"`{message_id}`" if message_id else "❌ Nicht gesetzt", inline=False)
        embed.add_field(
            name="Rolle",
            value=f"{role.mention} (`{role.id}`)" if role else "❌ Nicht gesetzt",
            inline=False,
        )
        embed.add_field(
            name="Log-Kanal",
            value=log_channel.mention if log_channel else "❌ Nicht gesetzt",
            inline=False,
        )
        if role:
            embed.add_field(name="Rollen-Position", value=f"`{role.position}`", inline=True)
            embed.add_field(name="Ist @everyone?", value="⚠️ Ja!" if role.is_default() else "✅ Nein", inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="verifyupdate")
    @commands.admin_or_permissions(manage_guild=True)
    async def verifyupdate(self, ctx):
        """Erzwingt ein manuelles Update der Verifikations-Nachricht."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        message_id = await self.config.guild(ctx.guild).message_id()

        if not channel_id or not message_id:
            return await ctx.send("❌ Die Verifikation wurde noch nicht eingerichtet.")

        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            return await ctx.send("❌ Der Kanal existiert nicht mehr.")

        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            return await ctx.send("❌ Die Nachricht existiert nicht mehr. Bitte `verifysetup` erneut ausführen.")

        view = VerificationLayoutView()
        try:
            await message.edit(content=None, embed=None, view=view)
            await ctx.send("✅ Verifikations-Nachricht wurde aktualisiert.")
        except Exception as e:
            await ctx.send(f"❌ Fehler beim Aktualisieren: {e}")

    @commands.command(name="verifyhelp")
    async def verifyhelp(self, ctx):
        """Zeigt die Befehle für das Verifikations-System an."""
        embed = discord.Embed(title="🛡️ Verifikation — Befehlsübersicht", color=discord.Color.dark_blue())
        embed.add_field(
            name="⚙️ Setup",
            value=(
                "`verifysetup #kanal @rolle` — Verifikation einrichten\n"
                "`verifyrole @rolle` — Verifikations-Rolle ändern\n"
                "`verifylog #kanal` — Log-Kanal einrichten\n"
                "`verifystatus` — Aktuellen Status anzeigen\n"
                "`verifyupdate` — Nachricht manuell aktualisieren"
            ),
            inline=False,
        )
        embed.add_field(
            name="📊 Statistiken",
            value="`verifystats` — Zeigt an, wie viele User verifiziert sind",
            inline=False,
        )
        embed.set_footer(text="User können sich über den Button in der Nachricht verifizieren.")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Verifikation(bot))
