import discord
import logging
from redbot.core import commands, Config

log = logging.getLogger("red.honigtopf")

# Prüfen, ob Discord Components V2 verfügbar ist
try:
    from discord.ui import LayoutView, Container, TextDisplay, Separator
    V2_AVAILABLE = True
except ImportError:
    V2_AVAILABLE = False


class Honigtopf(commands.Cog):
    """Ein Honeypot-Kanal, der Spam-Bots fängt und bestraft."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x484F4E45)

        default_guild = {
            "channel_id": None,
            "message_id": None,
            "kicks": 0,
            "log_channel": None,
        }
        self.config.register_guild(**default_guild)

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

        # Container 2: Kick-Zähler (Grauer Akzent)
        counter_container = Container(accent_color=discord.Color.dark_gray())
        counter_container.add_item(TextDisplay(f"🍯 **Kicks:** {kicks}"))

        view.add_item(main_container)
        view.add_item(counter_container)

        return view

    async def update_honigtopf_message(self, guild):
        """Aktualisiert die Honigtopf-Nachricht im Kanal."""
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
            log.warning(f"[Honigtopf] Keine Berechtigung für Nachricht {message_id}.")
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
            log.error(f"[Honigtopf] Fehler beim Editieren: {type(e).__name__}: {e}")

    @commands.Cog.listener()
    async def on_message(self, message):
        """Fängt Nachrichten im Honigtopf-Kanal ab."""
        if message.author.bot or not message.guild:
            return

        channel_id = await self.config.guild(message.guild).channel_id()
        if message.channel.id != channel_id:
            return

        if message.author.guild_permissions.administrator:
            return

        # 1. Nachricht löschen
        try:
            await message.delete()
        except discord.Forbidden:
            pass

        # 2. Bestrafung (Softban)
        member = message.author
        guild = message.guild
        punished = False

        try:
            # Neuere discord.py Versionen nutzen delete_message_seconds
            await member.ban(reason="Honigtopf ausgelöst (Softban)", delete_message_seconds=86400)
            await guild.unban(member, reason="Softban aufgehoben (Honigtopf)")
            punished = True
        except TypeError:
            # Fallback für ältere discord.py Versionen
            try:
                await member.ban(reason="Honigtopf ausgelöst (Softban)", delete_message_days=1)
                await guild.unban(member, reason="Softban aufgehoben (Honigtopf)")
                punished = True
            except discord.Forbidden:
                pass
        except discord.Forbidden:
            try:
                await member.kick(reason="Honigtopf ausgelöst (Kick-Fallback)")
                punished = True
            except discord.Forbidden:
                log.warning(f"[Honigtopf] Keine Berechtigung, {member} zu bestrafen.")

        if not punished:
            return

        # 3. Zähler erhöhen
        async with self.config.guild(guild).kicks() as kicks:
            kicks += 1
            new_kicks = kicks

        # 4. Nachricht aktualisieren
        await self.update_honigtopf_message(guild)

        # 5. Log-Nachricht senden
        log_channel_id = await self.config.guild(guild).log_channel()
        if log_channel_id:
            log_channel = guild.get_channel(log_channel_id)
            if log_channel:
                try:
                    await log_channel.send(f"🍯 **Honigtopf ausgelöst!** {member.mention} wurde gebannt. (Gesamt: {new_kicks})")
                except discord.Forbidden:
                    pass

        log.info(f"[Honigtopf] {member} wurde bestraft. Kicks: {new_kicks}")

    # ---------------- COMMANDS (Präfix 'h') ----------------

    @commands.command(name="hhonigtopfsetup")
    @commands.admin_or_permissions(manage_guild=True)
    async def hhonigtopfsetup(self, ctx, channel: discord.TextChannel):
        """Richtet den Honigtopf-Kanal ein."""
        embed = discord.Embed(
            title="⚠️ NICHT HIER SCHREIBEN ⚠️",
            description="Der Honigtopf wird initialisiert...",
            color=discord.Color.gold(),
        )
        msg = await channel.send(embed=embed)

        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).message_id.set(msg.id)
        await self.config.guild(ctx.guild).kicks.set(0)

        await self.update_honigtopf_message(ctx.guild)
        await ctx.send(f"✅ Honigtopf wurde in {channel.mention} eingerichtet. Kicks werden ab jetzt gezählt.")

    @commands.command(name="hhonigtopfupdate")
    @commands.admin_or_permissions(manage_guild=True)
    async def hhonigtopfupdate(self, ctx):
        """Erzwingt ein manuelles Update der Honigtopf-Nachricht."""
        await self.update_honigtopf_message(ctx.guild)
        await ctx.send("✅ Honigtopf-Nachricht wurde aktualisiert.")

    @commands.command(name="hhonigtopfreset")
    @commands.admin_or_permissions(manage_guild=True)
    async def hhonigtopfreset(self, ctx):
        """Setzt den Kick-Zähler des Honigtopfs auf 0 zurück."""
        await self.config.guild(ctx.guild).kicks.set(0)
        await self.update_honigtopf_message(ctx.guild)
        await ctx.send("✅ Der Kick-Zähler wurde auf **0** zurückgesetzt.")

    @commands.command(name="hhonigtopflog")
    @commands.admin_or_permissions(manage_guild=True)
    async def hhonigtopflog(self, ctx, channel: discord.TextChannel = None):
        """Setzt einen Log-Kanal für Honigtopf-Aktionen."""
        if channel is None:
            await self.config.guild(ctx.guild).log_channel.set(None)
            return await ctx.send("✅ Log-Kanal entfernt.")
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"✅ Log-Kanal auf {channel.mention} gesetzt.")

    @commands.command(name="hhonigtopfhelp")
    async def hhonigtopfhelp(self, ctx):
        """Zeigt die Honigtopf-Befehle an."""
        embed = discord.Embed(title="🍯 Honigtopf — Befehlsübersicht", color=discord.Color.gold())
        embed.add_field(
            name="⚙️ Setup",
            value=(
                "`hhonigtopfsetup #kanal` — Honigtopf einrichten\n"
                "`hhonigtopfupdate` — Nachricht manuell aktualisieren\n"
                "`hhonigtopfreset` — Kick-Zähler auf 0 setzen\n"
                "`hhonigtopflog #kanal` — Log-Kanal einrichten"
            ),
            inline=False,
        )
        embed.set_footer(text="Jede Nachricht im Honigtopf-Kanal führt zu einem Softban.")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Honigtopf(bot))
