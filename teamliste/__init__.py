import discord
import asyncio
import logging
from redbot.core import commands, Config
from discord.ext import tasks
from datetime import datetime, timezone

log = logging.getLogger("red.teamlist")

# Prüfen, ob Discord Components V2 verfügbar ist
try:
    from discord.ui import LayoutView, Container, TextDisplay, Separator
    V2_AVAILABLE = True
except ImportError:
    V2_AVAILABLE = False


class TeamList(commands.Cog):
    """Dynamische Teamliste mit Warn-System, Live-Updates & V2 Design."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x7A657573)

        default_guild = {
            "channel_id": None,
            "message_id": None,
            "tracked_roles": [],
            "max_warns": 3,
            "user_warns": {},
            "team_admin_role": None,
            "log_channel": None,
        }
        self.config.register_guild(**default_guild)

        self._update_tasks = {}
        self.update_loop.start()

    def cog_unload(self):
        self.update_loop.cancel()
        for task in self._update_tasks.values():
            task.cancel()

    # ---------------- HINTERGRUND-TASKS ----------------

    @tasks.loop(hours=1.0)
    async def update_loop(self):
        """Stündliches Update der Teamliste (Sicherheitsnetz)."""
        await self.bot.wait_until_red_ready()
        for guild in self.bot.guilds:
            try:
                await self.update_message(guild)
            except Exception as e:
                log.exception(f"[TeamList] Update für Guild {guild.id} fehlgeschlagen: {e}")

    def _schedule_update(self, guild, delay=5.0):
        """Plant ein Update mit Debounce (schützt vor Rate-Limits)."""
        key = guild.id
        old = self._update_tasks.get(key)
        if old and not old.done():
            old.cancel()
        self._update_tasks[key] = asyncio.create_task(self._delayed_update(guild, delay))

    async def _delayed_update(self, guild, delay):
        try:
            await asyncio.sleep(delay)
            await self.update_message(guild)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.exception(f"[TeamList] Delayed update fehlgeschlagen: {e}")

    # ---------------- LIVE-UPDATES ----------------

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.roles == after.roles:
            return
        tracked = await self.config.guild(after.guild).tracked_roles()
        if not tracked:
            return
        before_ids = {r.id for r in before.roles}
        after_ids = {r.id for r in after.roles}
        changed = before_ids.symmetric_difference(after_ids)
        if not (changed & set(tracked)):
            return
        self._schedule_update(after.guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        tracked = await self.config.guild(member.guild).tracked_roles()
        if any(r.id in tracked for r in member.roles):
            self._schedule_update(member.guild)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        tracked = await self.config.guild(member.guild).tracked_roles()
        if any(r.id in tracked for r in member.roles):
            self._schedule_update(member.guild)

    # ---------------- HELPER ----------------

    def _format_warns(self, current, max_warns):
        """Formatiert Warns mit farblicher Hervorhebung (ANSI)."""
        text = f"{current}/{max_warns}"
        ratio = current / max_warns if max_warns else 0

        if ratio >= 1.0:
            return f"**```ansi\n\u001b[1;31m{text}\u001b[0m```**"   # Rot + Fett
        elif ratio >= 0.6:
            return f"```ansi\n\u001b[1;33m{text}\u001b[0m```"        # Gelb/Orange
        elif ratio > 0:
            return f"```ansi\n\u001b[0;32m{text}\u001b[0m```"        # Grün
        else:
            return f"**{text}**"                                      # Neutral

    async def _is_team_admin(self, ctx):
        """Prüft, ob der User Team-Befehle ausführen darf."""
        if await self.bot.is_owner(ctx.author):
            return True
        if ctx.author.guild_permissions.administrator:
            return True
        if ctx.author.guild_permissions.manage_guild:
            return True
        role_id = await self.config.guild(ctx.guild).team_admin_role()
        if role_id:
            return any(r.id == role_id for r in ctx.author.roles)
        return False

    @staticmethod
    def _is_team_admin_check():
        """Decorator-Wrapper für den Check."""
        async def predicate(ctx):
            return await ctx.cog._is_team_admin(ctx)
        return commands.check(predicate)

    # ---------------- LISTEN-GENERIERUNG ----------------

    async def get_grouped_team(self, guild):
        """Gibt sortierte Rollen mit ihren Mitgliedern zurück (höchste Rolle pro User)."""
        tracked_roles = await self.config.guild(guild).tracked_roles()
        if not tracked_roles:
            return []

        roles = [guild.get_role(rid) for rid in tracked_roles]
        roles = [r for r in roles if r is not None]
        roles.sort(key=lambda r: r.position, reverse=True)

        member_roles = {role.id: [] for role in roles}

        for member in guild.members:
            if member.bot:
                continue
            for role in roles:
                if role in member.roles:
                    member_roles[role.id].append(member)
                    break

        grouped = []
        for role in roles:
            members = member_roles[role.id]
            if not members:
                continue
            members.sort(key=lambda m: m.display_name.lower())
            grouped.append((role, members))
        return grouped

    async def generate_team_list(self, guild):
        """Klassischer Text-Fallback (ohne V2)."""
        grouped = await self.get_grouped_team(guild)
        max_warns = await self.config.guild(guild).max_warns()
        user_warns = await self.config.guild(guild).user_warns()

        if not grouped:
            return "Es sind noch keine Rollen für die Teamliste konfiguriert oder besetzt."

        lines = []
        for role, members in grouped:
            lines.append(f"**{role.mention}**")
            for member in members:
                warns = user_warns.get(str(member.id), 0)
                lines.append(f"➔ {member.mention} | Warns: {warns}/{max_warns}")
            lines.append("")
        return "\n".join(lines)

    # ---------------- NACHRICHT-UPDATE ----------------

    async def update_message(self, guild):
        """Aktualisiert die Teamliste im konfigurierten Kanal."""
        channel_id = await self.config.guild(guild).channel_id()
        message_id = await self.config.guild(guild).message_id()

        if not channel_id or not message_id:
            log.warning(f"[TeamList] Kein Kanal/Nachricht für Guild {guild.id} konfiguriert.")
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            log.warning(f"[TeamList] Kanal {channel_id} nicht gefunden in Guild {guild.id}.")
            return

        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            log.warning(f"[TeamList] Nachricht {message_id} nicht gefunden – wurde sie gelöscht?")
            return
        except discord.Forbidden:
            log.warning(f"[TeamList] Keine Berechtigung, Nachricht {message_id} abzurufen.")
            return

        grouped = await self.get_grouped_team(guild)
        max_warns = await self.config.guild(guild).max_warns()
        user_warns = await self.config.guild(guild).user_warns()
        timestamp = int(datetime.now(timezone.utc).timestamp())

        # --- VERSUCH 1: Components V2 ---
        if V2_AVAILABLE:
            try:
                view = LayoutView()
                container = Container(accent_color=discord.Color.blue())

                container.add_item(TextDisplay("## 📋 Teamliste"))
                container.add_item(TextDisplay(f"-# Aktualisiert: <t:{timestamp}:R> • {len(grouped)} Kategorien"))

                if not grouped:
                    container.add_item(Separator())
                    container.add_item(TextDisplay("Keine Teammitglieder in den konfigurierten Rollen gefunden."))
                else:
                    for role, members in grouped:
                        container.add_item(Separator())
                        container.add_item(TextDisplay(f"### 🏷️ {role.mention}  `({len(members)})`"))
                        for member in members:
                            warns = user_warns.get(str(member.id), 0)
                            warn_text = self._format_warns(warns, max_warns)
                            container.add_item(TextDisplay(f"➔ {member.mention} | Warns: {warn_text}"))

                view.add_item(container)

                # WICHTIG: embed=None + content=None entfernt alte Inhalte!
                await message.edit(content=None, embed=None, view=view)
                return
            except Exception as e:
                log.error(f"[TeamList] V2-Edit fehlgeschlagen, nutze Embed-Fallback: {type(e).__name__}: {e}")

        # --- FALLBACK: Klassisches Embed ---
        description = await self.generate_team_list(guild)
        if len(description) > 4000:
            description = description[:4000] + "\n... (Liste zu lang)"

        embed = discord.Embed(
            title=f"📋 Teamliste (Aktualisiert: <t:{timestamp}:R>)",
            description=description,
            color=discord.Color.blue(),
        )
        try:
            await message.edit(content=None, embed=embed, view=None)
        except Exception as e:
            log.error(f"[TeamList] Auch Embed-Edit fehlgeschlagen: {type(e).__name__}: {e}")

    async def _log_action(self, guild, text):
        """Schreibt eine Aktion in den Log-Kanal."""
        log_channel_id = await self.config.guild(guild).log_channel()
        if not log_channel_id:
            return
        channel = guild.get_channel(log_channel_id)
        if not channel:
            return
        try:
            await channel.send(text)
        except discord.HTTPException:
            pass

    # ---------------- COMMANDS ----------------

    @commands.command(name="tteamsetup")
    @commands.admin_or_permissions(manage_guild=True)
    async def tteamsetup(self, ctx, channel: discord.TextChannel):
        """Richtet den Kanal für die Teamliste ein."""
        embed = discord.Embed(
            title="📋 Teamliste",
            description="Die Teamliste wird initialisiert...",
            color=discord.Color.blue(),
        )
        msg = await channel.send(embed=embed)

        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).message_id.set(msg.id)

        await self.update_message(ctx.guild)
        await ctx.send(f"✅ Teamliste wurde in {channel.mention} eingerichtet.")

    @commands.command(name="tteamaddrole")
    @commands.admin_or_permissions(manage_guild=True)
    async def tteamaddrole(self, ctx, *roles: discord.Role):
        """Fügt eine oder mehrere Rollen zur Teamliste hinzu."""
        if not roles:
            return await ctx.send("❌ Bitte gib mindestens eine Rolle an.\n**Beispiel:** `tteamaddrole @Rolle1 @Rolle2`")

        added, already = [], []
        async with self.config.guild(ctx.guild).tracked_roles() as tracked:
            for role in roles:
                if role.id not in tracked:
                    tracked.append(role.id)
                    added.append(role.mention)
                else:
                    already.append(role.mention)

        msg = []
        if added:
            msg.append(f"✅ **Hinzugefügt ({len(added)}):** {', '.join(added)}")
        if already:
            msg.append(f"⚠️ **Bereits vorhanden ({len(already)}):** {', '.join(already)}")
        await ctx.send("\n".join(msg))
        await self.update_message(ctx.guild)

    @commands.command(name="tteamremoverole")
    @commands.admin_or_permissions(manage_guild=True)
    async def tteamremoverole(self, ctx, *roles: discord.Role):
        """Entfernt eine oder mehrere Rollen aus der Teamliste."""
        if not roles:
            return await ctx.send("❌ Bitte gib mindestens eine Rolle an.")

        removed, not_found = [], []
        async with self.config.guild(ctx.guild).tracked_roles() as tracked:
            for role in roles:
                if role.id in tracked:
                    tracked.remove(role.id)
                    removed.append(role.mention)
                else:
                    not_found.append(role.mention)

        msg = []
        if removed:
            msg.append(f"✅ **Entfernt ({len(removed)}):** {', '.join(removed)}")
        if not_found:
            msg.append(f"⚠️ **Nicht in der Liste ({len(not_found)}):** {', '.join(not_found)}")
        await ctx.send("\n".join(msg))
        await self.update_message(ctx.guild)

    @commands.command(name="tteamroles")
    @commands.admin_or_permissions(manage_guild=True)
    async def tteamroles(self, ctx):
        """Zeigt alle konfigurierten Rollen (sortiert nach Hierarchie)."""
        tracked = await self.config.guild(ctx.guild).tracked_roles()
        if not tracked:
            return await ctx.send("📋 Es sind noch keine Rollen konfiguriert.")

        roles = [ctx.guild.get_role(rid) for rid in tracked]
        roles = [r for r in roles if r is not None]
        roles.sort(key=lambda r: r.position, reverse=True)

        lines = [f"`{i+1}.` {r.mention} `(Position: {r.position})`" for i, r in enumerate(roles)]
        embed = discord.Embed(
            title="📋 Konfigurierte Team-Rollen",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"Insgesamt: {len(roles)} Rollen")
        await ctx.send(embed=embed)

    @commands.command(name="tteamclearroles")
    @commands.admin_or_permissions(manage_guild=True)
    async def tteamclearroles(self, ctx):
        """Löscht ALLE konfigurierten Rollen aus der Teamliste."""
        tracked = await self.config.guild(ctx.guild).tracked_roles()
        if not tracked:
            return await ctx.send("❌ Es sind keine Rollen zum Löschen vorhanden.")

        await ctx.send(
            f"⚠️ Willst du wirklich **alle {len(tracked)} Rollen** entfernen?\n"
            f"Antworte mit `ja` zum Bestätigen (30 Sekunden Zeit)."
        )
        try:
            msg = await ctx.bot.wait_for(
                "message",
                timeout=30.0,
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
            )
        except asyncio.TimeoutError:
            return await ctx.send("⏱️ Zeit abgelaufen. Aktion abgebrochen.")

        if msg.content.lower() not in ("ja", "yes", "j", "y"):
            return await ctx.send("❌ Aktion abgebrochen.")

        await self.config.guild(ctx.guild).tracked_roles.set([])
        await ctx.send(f"✅ Alle {len(tracked)} Rollen wurden entfernt.")
        await self.update_message(ctx.guild)

    @commands.command(name="tteammaxwarns")
    @commands.admin_or_permissions(manage_guild=True)
    async def tteammaxwarns(self, ctx, anzahl: int):
        """Setzt die maximale Warn-Anzahl (Standard: 3)."""
        if anzahl < 1:
            return await ctx.send("❌ Die Anzahl muss mindestens 1 sein.")
        await self.config.guild(ctx.guild).max_warns.set(anzahl)
        await ctx.send(f"✅ Maximale Verwarnungen auf **{anzahl}** gesetzt.")
        await self.update_message(ctx.guild)

    @commands.command(name="tteamadminrole")
    @commands.admin_or_permissions(manage_guild=True)
    async def tteamadminrole(self, ctx, role: discord.Role = None):
        """Setzt eine Rolle, die Team-Befehle ausführen darf."""
        if role is None:
            await self.config.guild(ctx.guild).team_admin_role.set(None)
            return await ctx.send("✅ Team-Admin-Rolle entfernt. Nur noch Admins können Team-Befehle nutzen.")
        await self.config.guild(ctx.guild).team_admin_role.set(role.id)
        await ctx.send(f"✅ {role.mention} darf jetzt Team-Befehle ausführen.")

    @commands.command(name="tteamlogchannel")
    @commands.admin_or_permissions(manage_guild=True)
    async def tteamlogchannel(self, ctx, channel: discord.TextChannel = None):
        """Setzt einen Log-Kanal für Team-Aktionen."""
        if channel is None:
            await self.config.guild(ctx.guild).log_channel.set(None)
            return await ctx.send("✅ Log-Kanal entfernt.")
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"✅ Log-Kanal auf {channel.mention} gesetzt.")

    @commands.command(name="tteamwarn")
    @_is_team_admin_check()
    async def tteamwarn(self, ctx, member: discord.Member, anzahl: int = 1, *, grund: str = "Kein Grund angegeben"):
        """Fügt einem Mitglied Verwarnungen hinzu.

        Beispiel: tteamwarn @User 1 Spam im Chat
        """
        async with self.config.guild(ctx.guild).user_warns() as warns:
            current = warns.get(str(member.id), 0)
            warns[str(member.id)] = current + anzahl
            new_total = warns[str(member.id)]

        max_warns = await self.config.guild(ctx.guild).max_warns()
        await ctx.send(f"✅ {member.mention} hat jetzt **{new_total}/{max_warns}** Verwarnungen.\n📝 Grund: {grund}")
        await self._log_action(
            ctx.guild,
            f"⚠️ **Warn** | {member.mention} von {ctx.author.mention} | "
            f"({new_total}/{max_warns}) | Grund: {grund}"
        )
        await self.update_message(ctx.guild)

    @commands.command(name="tteamunwarn")
    @_is_team_admin_check()
    async def tteamunwarn(self, ctx, member: discord.Member, anzahl: int = 1):
        """Entfernt einzelne Verwarnungen von einem Mitglied."""
        async with self.config.guild(ctx.guild).user_warns() as warns:
            current = warns.get(str(member.id), 0)
            if current == 0:
                return await ctx.send(f"❌ {member.mention} hat keine Verwarnungen.")
            new_total = max(0, current - anzahl)
            if new_total == 0:
                del warns[str(member.id)]
            else:
                warns[str(member.id)] = new_total

        max_warns = await self.config.guild(ctx.guild).max_warns()
        await ctx.send(f"✅ {member.mention} hat jetzt **{new_total}/{max_warns}** Verwarnungen.")
        await self._log_action(
            ctx.guild,
            f"✅ **Unwarn** | {ctx.author.mention} entfernte {anzahl} Warn(s) von {member.mention} | "
            f"({new_total}/{max_warns})"
        )
        await self.update_message(ctx.guild)

    @commands.command(name="tteamresetwarns")
    @_is_team_admin_check()
    async def tteamresetwarns(self, ctx, member: discord.Member):
        """Setzt alle Verwarnungen eines Mitglieds zurück."""
        async with self.config.guild(ctx.guild).user_warns() as warns:
            if str(member.id) in warns:
                del warns[str(member.id)]
                await ctx.send(f"✅ Alle Verwarnungen von {member.mention} wurden zurückgesetzt.")
            else:
                return await ctx.send(f"❌ {member.mention} hat keine Verwarnungen.")

        await self._log_action(
            ctx.guild,
            f"🔄 **Reset** | {ctx.author.mention} hat alle Warns von {member.mention} zurückgesetzt."
        )
        await self.update_message(ctx.guild)

    @commands.command(name="tteamhistory")
    @_is_team_admin_check()
    async def tteamhistory(self, ctx, member: discord.Member):
        """Zeigt die aktuellen Verwarnungen eines Mitglieds."""
        user_warns = await self.config.guild(ctx.guild).user_warns()
        max_warns = await self.config.guild(ctx.guild).max_warns()
        current = user_warns.get(str(member.id), 0)

        embed = discord.Embed(
            title=f"📜 Warn-Historie von {member.display_name}",
            description=f"**{current}/{max_warns}** Verwarnungen aktiv.",
            color=discord.Color.orange() if current > 0 else discord.Color.green(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.command(name="tteamupdate")
    @_is_team_admin_check()
    async def tteamupdate(self, ctx):
        """Erzwingt ein manuelles Update der Teamliste."""
        await self.update_message(ctx.guild)
        await ctx.send("✅ Teamliste wurde manuell aktualisiert.")

    @commands.command(name="tteamhelp")
    async def tteamhelp(self, ctx):
        """Zeigt eine Übersicht aller Team-Befehle."""
        embed = discord.Embed(title="📋 TeamList — Befehlsübersicht", color=discord.Color.blue())
        embed.add_field(
            name="⚙️ Setup",
            value=(
                "`tteamsetup #kanal` — Teamliste einrichten\n"
                "`tteamaddrole @R1 @R2 @R3` — Rollen hinzufügen\n"
                "`tteamremoverole @R1 @R2` — Rollen entfernen\n"
                "`tteamroles` — Konfigurierte Rollen anzeigen\n"
                "`tteamclearroles` — Alle Rollen löschen"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔧 Einstellungen",
            value=(
                "`tteammaxwarns <zahl>` — Max. Warn-Anzahl\n"
                "`tteamadminrole @Rolle` — Team-Admin-Rolle setzen\n"
                "`tteamlogchannel #kanal` — Log-Kanal setzen"
            ),
            inline=False,
        )
        embed.add_field(
            name="⚠️ Warn-System",
            value=(
                "`tteamwarn @User [anzahl] [grund]` — Warn hinzufügen\n"
                "`tteamunwarn @User [anzahl]` — Warn entfernen\n"
                "`tteamresetwarns @User` — Alle Warns zurücksetzen\n"
                "`tteamhistory @User` — Warns eines Users anzeigen"
            ),
            inline=False,
        )
        embed.add_field(name="🔄 Sonstiges", value="`tteamupdate` — Manuelles Update der Liste", inline=False)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(TeamList(bot))
