import discord
import logging
from redbot.core import commands, Config

log = logging.getLogger("red.honeypot")

# Prüfen, ob Discord Components V2 verfügbar ist
try:
    from discord.ui import LayoutView, Container, TextDisplay, Separator
    V2_AVAILABLE = True
except ImportError:
    V2_AVAILABLE = False


class Honeypot(commands.Cog):
    """Ein Honeypot-Kanal, der Spam-Bots fängt und bestraft."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x484F4E45)  # HONE

        default_guild = {
            "channel_id": None,
            "message_id": None,
            "kicks": 0,
        }
        self.config.register_guild(**default_guild)

    # ---------------- NACHRICHT GENERIEREN ----------------

    def get_honeypot_view(self, kicks):
        """Erstellt das cleane Container-Design für den Honeypot."""
        if not V2_AVAILABLE:
            return None

        view = LayoutView()

        # Container 1: Hauptwarnung (Gelber Akzent)
        main_container = Container(accent_color=discord.Color.gold())
        main_container.add_item(TextDisplay("## ⚠️ NICHT HIER SCHREIBEN ⚠️"))
        main_container.add_item(TextDisplay(
            "Dieser Kanal dient dazu, Spam-Bots zu fangen.\n"
            "Jegliche Nachrichten, die hier gesendet werden, führen zu einem **Softban**."
        ))
        main_container.add_item(Separator())

        # Container 2: Kick-Zähler (Grauer Akzent, wie im Bild)
        counter_container = Container(accent_color=discord.Color.dark_gray())
        counter_container.add_item(TextDisplay(f"🍯 **Kicks:** {kicks}"))

        view.add_item(main_container)
        view.add_item(counter_container)

        return view

    async def update_honeypot_message(self, guild):
        """Aktualisiert die Honeypot-Nachricht im Kanal."""
        channel_id = await self.config.guild(guild).channel_id()
        message_id = await self.config.guild(guild).message_id()
        kicks = await self.config.guild(guild).kicks()

        if not channel_id or not message_id:
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            return

        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            return
        except discord.Forbidden:
            log.warning(f"[Honeypot] Keine Berechtigung für Nachricht {message_id}.")
            return

        view = self.get_honeypot_view(kicks)

        try:
            if view:
                await message.edit(content=None, embed=None, view=view)
            else:
                # Fallback, falls V2 nicht verfügbar ist
                embed = discord.Embed(
                    title="⚠️ NICHT HIER SCHREIBEN ⚠️",
                    description=(
                        "Dieser Kanal dient dazu, Spam-Bots zu fangen.\n"
                        "Jegliche Nachrichten, die hier gesendet werden, führen zu einem **Softban**."
                    ),
                    color=discord.Color.gold(),
                )
                embed.set_footer(text=f"🍯 Kicks: {kicks}")
                await message.edit(content=None, embed=embed, view=None)
        except Exception as e:
            log.error(f"[Honeypot] Fehler beim Editieren: {type(e).__name__}: {e}")

    # ---------------- EVENT LISTENER ----------------

    @commands.Cog.listener()
    async def on_message(self, message):
        """Fängt Nachrichten im Honeypot-Kanal ab."""
        if message.author.bot or not message.guild:
            return

        channel_id = await self.config.guild(message.guild).channel_id()
        if message.channel.id != channel_id:
            return

        # Admins und Moderatoren ignorieren (optional, aber empfohlen)
        if message.author.guild_permissions.administrator:
            return

        # 1. Nachricht löschen
        try:
            await message.delete()
        except discord.Forbidden:
            pass

        # 2. Bestrafung (Softban: Ban + Unban, um Nachrichten zu löschen)
        member = message.author
        guild = message.guild
        punished = False

        try:
            # Softban-Logik (Ban mit 1 Tag Nachrichten-Löschung, dann Unban)
            await member.ban(reason="Honeypot ausgelöst (Softban)", delete_message_days=1)
            await guild.unban(member, reason="Softban aufgehoben (Honeypot)")
            punished = True
        except discord.Forbidden:
            # Fallback: Kick, falls keine Ban-Rechte vorhanden sind
            try:
                await member.kick(reason="Honeypot ausgelöst (Kick-Fallback)")
                punished = True
            except discord.Forbidden:
                log.warning(f"[Honeypot] Keine Berechtigung, {member} zu bestrafen.")

        if not punished:
            return

        # 3. Zähler erhöhen
        async with self.config.guild(guild).kicks() as kicks:
            kicks += 1
            new_kicks = kicks

        # 4. Nachricht aktualisieren
        await self.update_honeypot_message(guild)

        # 5. Log-Nachricht (optional)
        log_channel_id = await self.config.guild(guild).log_channel() if hasattr(self.config.guild(guild), 'log_channel') else None
        # (Falls du einen Log-Kanal in der Config hast, kannst du hier loggen)
        log.info(f"[Honeypot] {member} wurde wegen einer Nachricht im Honeypot-Kanal bestraft. Kicks: {new_kicks}")

    # ---------------- COMMANDS (Präfix 'h') ----------------

    @commands.command(name="hhoneypotsetup")
    @commands.admin_or_permissions(manage_guild=True)
    async def hhoneypotsetup(self, ctx, channel: discord.TextChannel):
        """Richtet den Honeypot-Kanal ein.

        Beispiel: hhoneypotsetup #honeypot
        """
        # Sende die erste Nachricht (Initialisierung)
        embed = discord.Embed(
            title="⚠️ NICHT HIER SCHREIBEN ⚠️",
            description="Der Honeypot wird initialisiert...",
            color=discord.Color.gold(),
        )
        msg = await channel.send(embed=embed)

        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).message_id.set(msg.id)
        await self.config.guild(ctx.guild).kicks.set(0)

        await self.update_honeypot_message(ctx.guild)
        await ctx.send(f"✅ Honeypot wurde in {channel.mention} eingerichtet. Kicks werden ab jetzt gezählt.")

    @commands.command(name="hhoneypotupdate")
    @commands.admin_or_permissions(manage_guild=True)
    async def hhoneypotupdate(self, ctx):
        """Erzwingt ein manuelles Update der Honeypot-Nachricht."""
        await self.update_honeypot_message(ctx.guild)
        await ctx.send("✅ Honeypot-Nachricht wurde aktualisiert.")

    @commands.command(name="hhoneypotreset")
    @commands.admin_or_permissions(manage_guild=True)
    async def hhoneypotreset(self, ctx):
        """Setzt den Kick-Zähler des Honeypots auf 0 zurück."""
        await self.config.guild(ctx.guild).kicks.set(0)
        await self.update_honeypot_message(ctx.guild)
        await ctx.send("✅ Der Kick-Zähler wurde auf **0** zurückgesetzt.")

    @commands.command(name="hhoneypothelp")
    async def hhoneypothelp(self, ctx):
        """Zeigt die Honeypot-Befehle an."""
        embed = discord.Embed(title="🍯 Honeypot — Befehlsübersicht", color=discord.Color.gold())
        embed.add_field(
            name="⚙️ Setup",
            value=(
                "`hhoneypotsetup #kanal` — Honeypot einrichten\n"
                "`hhoneypotupdate` — Nachricht manuell aktualisieren\n"
                "`hhoneypotreset` — Kick-Zähler auf 0 setzen"
            ),
            inline=False,
        )
        embed.set_footer(text="Jede Nachricht im Honeypot-Kanal führt zu einem Softban.")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Honeypot(bot))
