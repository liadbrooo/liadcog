import discord
from redbot.core import commands, Config
from typing import Union
import asyncio

class MetaGamingSchutz(commands.Cog):
    """Wirft Nutzer nach X Minuten aus dem Voice-Channel, wenn sie FiveM spielen (inkl. Whitelist, DM & Toggle)."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        # HIER IST DIE ÄNDERUNG: enabled=False (Standardmäßig aus)
        self.config.register_guild(whitelist=[], timeout_minutes=5, enabled=False)
        
        # Interner Speicher für aktive Timer (member_id -> asyncio.Task)
        self.pending_tasks = {}

    # --- Hilfsfunktionen ---
    def _has_fivem(self, member: discord.Member) -> bool:
        """Prüft, ob der Nutzer FiveM spielt."""
        for activity in member.activities:
            if activity.type == discord.ActivityType.playing:
                name = getattr(activity, 'name', '') or ''
                state = getattr(activity, 'state', '') or ''
                details = getattr(activity, 'details', '') or ''
                combined_text = f"{name} {state} {details}".lower()
                if "fivem" in combined_text or "cfx.re" in combined_text:
                    return True
        return False

    async def _is_whitelisted(self, member: discord.Member) -> bool:
        """Prüft, ob der Nutzer oder seine Rollen auf der Whitelist stehen."""
        whitelist = await self.config.guild(member.guild).whitelist()
        if member.id in whitelist:
            return True
        user_role_ids = [role.id for role in member.roles]
        if any(role_id in whitelist for role_id in user_role_ids):
            return True
        return False

    def _cancel_task(self, member_id: int):
        """Bricht einen laufenden Timer ab, falls vorhanden."""
        if member_id in self.pending_tasks:
            self.pending_tasks[member_id].cancel()
            del self.pending_tasks[member_id]

    # --- Konfigurations Befehle ---
    @commands.group(name="metawhitelist", aliases=["mwl"])
    @commands.admin_or_permissions(manage_guild=True)
    async def metawhitelist(self, ctx: commands.Context):
        """Verwaltung der Whitelist für den MetaGaming-Schutz."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @metawhitelist.command(name="add")
    async def metawhitelist_add(self, ctx: commands.Context, *, target: Union[discord.Member, discord.Role]):
        """Fügt einen Nutzer oder eine Rolle zur Whitelist hinzu."""
        whitelist = await self.config.guild(ctx.guild).whitelist()
        if target.id in whitelist:
            return await ctx.send(f"{target.name} ist bereits auf der Whitelist.")
        whitelist.append(target.id)
        await self.config.guild(ctx.guild).whitelist.set(whitelist)
        await ctx.send(f"✅ `{target.name}` wurde zur MetaGaming-Whitelist hinzugefügt.")

    @metawhitelist.command(name="remove")
    async def metawhitelist_remove(self, ctx: commands.Context, *, target: Union[discord.Member, discord.Role]):
        """Entfernt einen Nutzer oder eine Rolle von der Whitelist."""
        whitelist = await self.config.guild(ctx.guild).whitelist()
        if target.id not in whitelist:
            return await ctx.send(f"{target.name} ist nicht auf der Whitelist.")
        whitelist.remove(target.id)
        await self.config.guild(ctx.guild).whitelist.set(whitelist)
        await ctx.send(f"❌ `{target.name}` wurde von der MetaGaming-Whitelist entfernt.")

    @metawhitelist.command(name="list")
    async def metawhitelist_list(self, ctx: commands.Context):
        """Zeigt alle Nutzer und Rollen auf der Whitelist an."""
        whitelist = await self.config.guild(ctx.guild).whitelist()
        if not whitelist:
            return await ctx.send("Die Whitelist ist aktuell leer.")
        msg = "**MetaGaming-Whitelist:**\n"
        for item_id in whitelist:
            obj = ctx.guild.get_role(item_id) or ctx.guild.get_member(item_id)
            if obj:
                obj_type = "Rolle" if isinstance(obj, discord.Role) else "Nutzer"
                msg += f"- {obj_type}: `{obj.name}`\n"
            else:
                msg += f"- Unbekannte ID: `{item_id}`\n"
        await ctx.send(msg)

    @commands.command(name="metatimeout")
    @commands.admin_or_permissions(manage_guild=True)
    async def metatimeout(self, ctx: commands.Context, minutes: int):
        """Setzt die Zeit in Minuten, bevor man aus dem Call geworfen wird (Standard: 5)."""
        if minutes < 1:
            return await ctx.send("Die Zeit muss mindestens 1 Minute betragen.")
        await self.config.guild(ctx.guild).timeout_minutes.set(minutes)
        await ctx.send(f"✅ Das MetaGaming-Timeout wurde auf `{minutes} Minuten` gesetzt.")

    @commands.command(name="metatoggle")
    @commands.admin_or_permissions(manage_guild=True)
    async def metatoggle(self, ctx: commands.Context):
        """Schaltet den MetaGaming-Schutz AN oder AUS."""
        current_state = await self.config.guild(ctx.guild).enabled()
        new_state = not current_state
        await self.config.guild(ctx.guild).enabled.set(new_state)
        
        if new_state:
            await ctx.send("✅ Der MetaGaming-Schutz ist jetzt **aktiviert**.")
        else:
            # Wenn deaktiviert, alle laufenden Timer für diesen Server abbrechen
            for member_id, task in list(self.pending_tasks.items()):
                member = ctx.guild.get_member(member_id)
                if member and member.guild.id == ctx.guild.id:
                    task.cancel()
                    del self.pending_tasks[member_id]
            await ctx.send("❌ Der MetaGaming-Schutz ist jetzt **deaktiviert**. Alle laufenden Timer wurden abgebrochen.")

    # --- Timer und Kick-Logik ---
    async def _delayed_kick(self, member: discord.Member):
        """Wartet die eingestellte Zeit und kickt den Nutzer dann, falls er noch im Call ist."""
        try:
            timeout = await self.config.guild(member.guild).timeout_minutes()
            await asyncio.sleep(timeout * 60)
            
            # Nach Ablauf der Zeit prüfen, ob der Schutz in der Zwischenzeit deaktiviert wurde
            if not await self.config.guild(member.guild).enabled():
                return

            # Nutzer neu vom Server holen, um den AKTUELLEN Status zu haben
            member = member.guild.get_member(member.id)
            if not member: 
                return # Nutzer hat den Server verlassen

            # Prüfen, ob er wirklich noch im Voice-Channel ist
            if member.voice is None or member.voice.channel is None:
                return

            # Prüfen, ob er IMMER NOCH FiveM spielt
            if not self._has_fivem(member):
                return # Er hat das Spiel in der Zwischenzeit beendet

            # Bot-Rechte prüfen
            if not member.guild.me.guild_permissions.move_members:
                return

            # --- Kicken ---
            await member.move_to(None, reason="MetaGaming-Schutz: FiveM nach Timeout erkannt.")
            
            # --- Geordnete DM senden ---
            try:
                embed = discord.Embed(
                    title="⚠️ Automatisierter Voice-Kick",
                    description=(
                        f"Hallo {member.mention},\n\n"
                        "du wurdest soeben **automatisiert** aus dem Voice-Channel entfernt.\n\n"
                        "**Grund:** Verdacht auf Meta-Gaming.\n"
                        "Unser System hat registriert, dass du das Spiel **FiveM** spielst. "
                        "Um eine unfaire Beeinflussung durch Meta-Gaming zu verhindern, "
                        "wird der Voice-Zugang für Spieler temporär gesperrt.\n\n"
                        "*Dies ist eine automatisierte Nachricht. Antworten auf diese Nachricht werden nicht gelesen.*"
                    ),
                    color=discord.Color.red()
                )
                await member.send(embed=embed)
            except discord.Forbidden:
                pass # DMs gesperrt
                
        except asyncio.CancelledError:
            # Wird geworfen, wenn der Task abgebrochen wurde (z.B. Nutzer verlässt Call, Spiel beendet, Toggle aus)
            pass
        except Exception:
            pass # Andere Fehler abfangen, damit der Bot nicht crasht
        finally:
            # Aufräumen, falls die ID noch existiert
            if member.id in self.pending_tasks:
                del self.pending_tasks[member.id]

    # --- Event Listener ---
    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        # 0. Ist der Schutz auf diesem Server aktiviert?
        if not await self.config.guild(after.guild).enabled():
            return

        # 1. Wurde FiveM gerade gestartet oder beendet?
        was_fivem = self._has_fivem(before)
        is_fivem = self._has_fivem(after)

        # Wenn FiveM gestartet wurde
        if is_fivem and not was_fivem:
            if after.voice and after.voice.channel:
                if await self._is_whitelisted(after):
                    return
                # Timer starten, falls noch nicht aktiv
                if after.id not in self.pending_tasks:
                    task = asyncio.create_task(self._delayed_kick(after))
                    self.pending_tasks[after.id] = task

        # Wenn FiveM beendet wurde
        elif not is_fivem and was_fivem:
            self._cancel_task(after.id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        # 0. Ist der Schutz auf diesem Server aktiviert?
        if not await self.config.guild(member.guild).enabled():
            return

        # Wenn JEMAND einen Call betritt
        if after.channel is not None and before.channel is None:
            if self._has_fivem(member):
                if await self._is_whitelisted(member):
                    return
                if member.id not in self.pending_tasks:
                    task = asyncio.create_task(self._delayed_kick(member))
                    self.pending_tasks[member.id] = task
                    
        # Wenn JEMAND einen Call verlässt
        elif before.channel is not None and after.channel is None:
            self._cancel_task(member.id)

# Setup-Funktion für RedBot
async def setup(bot):
    await bot.add_cog(MetaGamingSchutz(bot))
