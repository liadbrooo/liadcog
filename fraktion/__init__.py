import discord
from redbot.core import commands, Config
from redbot.core.utils.chat_formatting import pagify
from datetime import datetime, timedelta
import uuid
import re
import traceback

class Fraktion(commands.Cog):
    """Fraktionsverwaltung für FiveM DE-RP Server (Haupt- und Fraktions-Discords)"""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)

        default_global = {
            "factions": {}
        }
        default_guild = {
            "legal_board_channel": None,
            "illegal_board_channel": None,
            "changelog_channel": None,
            "warn_log_channel": None,
            "warn_roles": []
        }
        default_user = {
            "blacklists": []
        }

        self.config.register_global(**default_global)
        self.config.register_guild(**default_guild)
        self.config.register_user(**default_user)

    # --- HELPER ---
    def get_berlin_time(self):
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo("Europe/Berlin"))
        except Exception:
            from datetime import timezone
            return datetime.now(timezone(timedelta(hours=2)))

    async def is_faction_leader(self, user: discord.User, faction_data: dict) -> bool:
        guild = self.bot.get_guild(faction_data.get("guild_id", 0))
        if not guild:
            return False
        try:
            member = await guild.fetch_member(user.id)
        except (discord.NotFound, discord.Forbidden):
            return False
        leader_ids = faction_data.get("leader_role_ids", [])
        return any(role.id in leader_ids for role in member.roles)

    def parse_duration(self, duration_str: str):
        if duration_str.lower() in ["perm", "permanent", "0"]:
            return None
        match = re.match(r"(\d+)([dhmwM])", duration_str)
        if not match:
            return False
        amount = int(match.group(1))
        unit = match.group(2)
        if unit == "m": delta = timedelta(minutes=amount)
        elif unit == "h": delta = timedelta(hours=amount)
        elif unit == "d": delta = timedelta(days=amount)
        elif unit == "w": delta = timedelta(weeks=amount)
        elif unit == "M": delta = timedelta(days=30 * amount)
        berlin_now = self.get_berlin_time()
        return berlin_now + delta

    # --- CUSTOM CHECK ---
    def is_warn_authorized():
        async def predicate(ctx):
            if ctx.author.guild_permissions.manage_guild:
                return True
            authorized_roles = await ctx.cog.config.guild(ctx.guild).warn_roles()
            if authorized_roles:
                if any(role.id in authorized_roles for role in ctx.author.roles):
                    return True
                return False
            return ctx.author.guild_permissions.manage_messages
        return commands.check(predicate)

    # --- EINSTELLUNGEN ---
    @commands.group(name="fraktion", aliases=["fk"])
    @commands.guild_only()
    async def faction_group(self, ctx):
        pass

    @faction_group.group(name="setup")
    @commands.admin_or_permissions(manage_guild=True)
    async def faction_setup(self, ctx):
        pass

    @faction_setup.command(name="stadtblatt")
    async def setup_stadtblatt(self, ctx, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).legal_board_channel.set(channel.id)
        await ctx.send(f"✅ Das Stadtblatt wurde auf {channel.mention} gesetzt.")

    @faction_setup.command(name="schwarzesbrett")
    async def setup_schwarzesbrett(self, ctx, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).illegal_board_channel.set(channel.id)
        await ctx.send(f"✅ Das Schwarze Brett wurde auf {channel.mention} gesetzt.")

    @faction_setup.command(name="changelog")
    async def setup_changelog(self, ctx, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).changelog_channel.set(channel.id)
        await ctx.send(f"✅ Der Fraktions-Changelog-Channel wurde auf {channel.mention} gesetzt.")

    @faction_setup.command(name="warnlog")
    async def setup_warnlog(self, ctx, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).warn_log_channel.set(channel.id)
        await ctx.send(f"✅ Der Warn-Log-Channel wurde auf {channel.mention} gesetzt.")

    @faction_setup.command(name="warnroles", aliases=["warnrollen"])
    async def setup_warnroles(self, ctx, roles: commands.Greedy[discord.Role]):
        if not roles:
            await self.config.guild(ctx.guild).warn_roles.set([])
            return await ctx.send("✅ Autorisierten Rollen zurückgesetzt. Standard: Nachrichten verwalten.")
        role_ids = [r.id for r in roles]
        await self.config.guild(ctx.guild).warn_roles.set(role_ids)
        await ctx.send(f"✅ Warn-berechtigte Rollen: {', '.join(r.mention for r in roles)}")

    # --- FRAKTIONSVERWALTUNG ---
    @faction_group.command(name="add")
    @commands.admin_or_permissions(manage_guild=True)
    async def faction_add(self, ctx, name: str, typ: str, guild_id: int, *, leader_role_ids: str):
        try:
            role_ids = [int(r.strip()) for r in leader_role_ids.split(",")]
        except ValueError:
            return await ctx.send("❌ Fehler beim Parsen der Rollen-IDs. Format: 123, 456")
        if typ.lower() not in ["legal", "illegal"]:
            return await ctx.send("❌ Typ muss `legal` oder `illegal` sein.")
        async with self.config.factions() as factions:
            if name.lower() in factions:
                return await ctx.send("❌ Fraktion existiert bereits.")
            factions[name.lower()] = {
                "display_name": name,
                "type": typ.lower(),
                "guild_id": guild_id,
                "leader_role_ids": role_ids,
                "warnings": []
            }
        await ctx.send(f"✅ Fraktion **{name}** ({typ}) hinzugefügt.")

    @faction_group.command(name="remove")
    @commands.admin_or_permissions(manage_guild=True)
    async def faction_remove(self, ctx, name: str):
        async with self.config.factions() as factions:
            if name.lower() not in factions:
                return await ctx.send("❌ Fraktion existiert nicht.")
            del factions[name.lower()]
        await ctx.send(f"✅ Fraktion **{name}** entfernt.")

    @faction_group.command(name="list", aliases=["liste"])
    async def faction_list(self, ctx):
        """Zeigt alle Fraktionen mit Leitungsrollen (echte Namen statt Pings)."""
        factions = await self.config.factions()
        if not factions:
            return await ctx.send("Keine Fraktionen registriert.")

        faction_items = list(factions.items())
        pages = []

        for i in range(0, len(faction_items), 25):
            chunk = faction_items[i:i+25]
            embed = discord.Embed(
                title="🚓 Fraktionsübersicht" if i == 0 else "🚓 Fraktionsübersicht (Fortsetzung)",
                color=discord.Color.blue(),
                timestamp=self.get_berlin_time()
            )
            if i == 0:
                embed.set_footer(text=f"{len(factions)} Fraktionen insgesamt")

            for key, data in chunk:
                guild = self.bot.get_guild(data.get("guild_id", 0))
                emoji = "🛡️" if data.get("type") == "legal" else "💀"
                name = data.get('display_name', key)

                desc = f"**Server:** `{guild.name if guild else 'Nicht gefunden'}`\n"

                # Leitungsrollen echte Namen holen
                leader_ids = data.get("leader_role_ids", [])
                if leader_ids and guild:
                    leader_names = []
                    for rid in leader_ids:
                        role = guild.get_role(rid)
                        if role:
                            leader_names.append(role.name)
                        else:
                            leader_names.append(f"ID:{rid}")
                    desc += f"**Leitung:** {', '.join(leader_names)}\n"
                elif leader_ids:
                    desc += f"**Leitung:** (Server nicht erreichbar, IDs: {', '.join(map(str, leader_ids))})\n"
                else:
                    desc += "**Leitung:** *Keine definiert*\n"

                warns = len(data.get('warnings', []))
                desc += f"**Verwarnungen:** `{warns}`\n"
                desc += "――――――――――――――――"  # optische Trennung

                embed.add_field(name=f"{emoji} {name}", value=desc, inline=False)

            pages.append(embed)

        for page in pages:
            await ctx.send(embed=page)

    # --- VERWARNUNGEN ---
    @faction_group.command(name="warn", aliases=["verwarnung"])
    @is_warn_authorized()
    async def faction_warn(self, ctx, faction: str, *, reason_text: str):
        try:
            factions = await self.config.factions()
            if faction.lower() not in factions:
                return await ctx.send("❌ Fraktion existiert nicht.")
            faction_data = factions[faction.lower()]

            parts = reason_text.split()
            duration = "perm"
            reason = reason_text
            if len(parts) > 1:
                test_dur = self.parse_duration(parts[0])
                if test_dur is not False:
                    duration = parts[0]
                    reason = " ".join(parts[1:])
            if not reason:
                return await ctx.send("❌ Grund fehlt.")

            expires_at = self.parse_duration(duration)
            warning_id = str(uuid.uuid4())[:8]
            current_time = self.get_berlin_time()
            current_time_str = current_time.strftime("%d.%m.%Y %H:%M")
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M") if expires_at else "Permanent"

            async with self.config.factions() as f:
                if "warnings" not in f[faction.lower()]:
                    f[faction.lower()]["warnings"] = []
                f[faction.lower()]["warnings"].append({
                    "id": warning_id,
                    "reason": reason,
                    "moderator": ctx.author.name,
                    "date": current_time_str,
                    "expires": expires_at.timestamp() if expires_at else None
                })

            embed = discord.Embed(title="⚠️ Verwarnung ausgesprochen", color=discord.Color.red(), timestamp=current_time)
            embed.add_field(name="Fraktion", value=faction_data.get('display_name', faction), inline=True)
            embed.add_field(name="Warn-ID", value=warning_id, inline=True)
            embed.add_field(name="Dauer", value=expires_str, inline=True)
            embed.add_field(name="Grund", value=reason, inline=False)

            # Log-Channel mit Rollen-Ping (funktioniert nur, wenn der Log-Channel auf dem gleichen Server ist wie die Rollen)
            log_channel_id = await self.config.guild(ctx.guild).warn_log_channel()
            if log_channel_id:
                log_channel = self.bot.get_channel(log_channel_id)
                if log_channel:
                    log_embed = discord.Embed(title="🚨 Neue Fraktionsverwarnung", color=discord.Color.dark_red(), timestamp=current_time)
                    log_embed.add_field(name="Fraktion", value=faction_data.get('display_name', faction), inline=True)
                    log_embed.add_field(name="Warn-ID", value=warning_id, inline=True)
                    log_embed.add_field(name="Dauer", value=expires_str, inline=True)
                    log_embed.add_field(name="Grund", value=reason, inline=False)
                    log_embed.set_footer(text=f"Ausgestellt von {ctx.author.name}")

                    # Ping nur senden, wenn die Rollen auf diesem Server existieren
                    leader_ids = faction_data.get("leader_role_ids", [])
                    if leader_ids and log_channel.guild.id == faction_data.get("guild_id"):
                        pings = " ".join(f"<@&{rid}>" for rid in leader_ids)
                        await log_channel.send(content=pings, embed=log_embed)
                    else:
                        await log_channel.send(embed=log_embed)

            await ctx.send(embed=embed)

        except Exception as e:
            error_msg = f"```py\n{traceback.format_exc()}\n```"
            if len(error_msg) > 1900:
                error_msg = error_msg[-1900:]
            await ctx.send(f"❌ Interner Fehler:\n{error_msg}")

    @faction_group.command(name="warns", aliases=["akte"])
    async def faction_warns(self, ctx, faction: str):
        factions = await self.config.factions()
        if faction.lower() not in factions:
            return await ctx.send("❌ Fraktion existiert nicht.")
        warnings = factions[faction.lower()].get("warnings", [])
        if not warnings:
            return await ctx.send(f"✅ Fraktion **{faction}** hat keine Verwarnungen.")

        embed = discord.Embed(title=f"Fraktionsakte: {faction}", color=discord.Color.orange())
        now = datetime.now().timestamp()
        for w in warnings:
            status = "**[ABGELAUFEN]**" if w.get("expires") and w["expires"] < now else "**[AKTIV]**"
            exp = f"Läuft ab: {datetime.fromtimestamp(w['expires']).strftime('%d.%m.%Y %H:%M')}" if w.get("expires") else "Permanent"
            embed.add_field(
                name=f"{status} ID: `{w['id']}`",
                value=f"**Grund:** {w['reason']}\n**Dauer:** {exp}\n**Von:** {w['moderator']} am {w['date']}",
                inline=False
            )
        await ctx.send(embed=embed)

    @faction_group.command(name="unwarn", aliases=["removewarn"])
    @commands.admin_or_permissions(manage_guild=True)
    async def faction_unwarn(self, ctx, faction: str, warning_id: str):
        async with self.config.factions() as f:
            if faction.lower() not in f:
                return await ctx.send("❌ Fraktion existiert nicht.")
            warnings = f[faction.lower()].get("warnings", [])
            initial_len = len(warnings)
            warnings[:] = [w for w in warnings if w["id"] != warning_id]
            if len(warnings) == initial_len:
                return await ctx.send("❌ Verwarnung nicht gefunden.")
        await ctx.send(f"✅ Verwarnung `{warning_id}` entfernt.")

    # --- BLACKLIST ---
    @faction_group.group(name="blacklist", aliases=["bl"])
    @commands.admin_or_permissions(manage_messages=True)
    async def faction_blacklist(self, ctx):
        pass

    @faction_blacklist.command(name="add")
    async def bl_add(self, ctx, user: discord.User, faction: str, *, reason: str):
        factions = await self.config.factions()
        if faction.lower() not in factions:
            return await ctx.send("❌ Fraktion existiert nicht.")
        current_time = self.get_berlin_time().strftime("%d.%m.%Y")
        async with self.config.user(user).blacklists() as blacklists:
            if any(b["faction"].lower() == faction.lower() for b in blacklists):
                return await ctx.send(f"❌ {user.name} ist bereits auf der Blacklist von {faction}.")
            blacklists.append({
                "faction": factions[faction.lower()].get("display_name", faction),
                "reason": reason,
                "moderator": ctx.author.name,
                "date": current_time
            })
        await ctx.send(f"🚫 {user.mention} auf Blacklist von **{faction}** gesetzt.\nGrund: {reason}")

    @faction_blacklist.command(name="remove")
    async def bl_remove(self, ctx, user: discord.User, faction: str):
        async with self.config.user(user).blacklists() as blacklists:
            initial_len = len(blacklists)
            blacklists[:] = [b for b in blacklists if b["faction"].lower() != faction.lower()]
            if len(blacklists) == initial_len:
                return await ctx.send("❌ Kein Eintrag gefunden.")
        await ctx.send(f"✅ {user.name} von Blacklist **{faction}** entfernt.")

    @faction_blacklist.command(name="check", aliases=["info"])
    async def bl_check(self, ctx, user: discord.User):
        blacklists = await self.config.user(user).blacklists()
        if not blacklists:
            return await ctx.send(f"✅ {user.name} steht auf keiner Fraktions-Blacklist.")
        embed = discord.Embed(title=f"🚫 Blacklist: {user.name}", color=discord.Color.dark_red())
        for b in blacklists:
            embed.add_field(
                name=f"Fraktion: {b['faction']}",
                value=f"Grund: {b['reason']}\nVon: {b['moderator']} am {b['date']}",
                inline=False
            )
        await ctx.send(embed=embed)

    # --- MELDUNGEN & CHANGELOG ---
    @faction_group.command(name="meldung")
    async def faction_meldung(self, ctx, faction: str, *, text: str):
        factions = await self.config.factions()
        if faction.lower() not in factions:
            return await ctx.send("❌ Fraktion existiert nicht.")
        faction_data = factions[faction.lower()]
        if not await self.is_faction_leader(ctx.author, faction_data):
            return await ctx.send("❌ Du bist kein Leader dieser Fraktion.")

        channel_id = await self.config.guild(ctx.guild).legal_board_channel() if faction_data.get("type") == "legal" else await self.config.guild(ctx.guild).illegal_board_channel()
        if not channel_id:
            return await ctx.send("❌ Kein Kanal für das Stadtblatt/Schwarze Brett eingerichtet.")
        target_channel = self.bot.get_channel(channel_id)
        if not target_channel:
            return await ctx.send("❌ Zielkanal nicht gefunden.")

        # Rollen als Text erwähnen, da Pings serverübergreifend nicht funktionieren
        leader_ids = faction_data.get("leader_role_ids", [])
        leader_text = ""
        if leader_ids:
            guild = self.bot.get_guild(faction_data.get("guild_id"))
            if guild:
                leader_names = []
                for rid in leader_ids:
                    role = guild.get_role(rid)
                    leader_names.append(f"@{role.name}" if role else f"ID:{rid}")
                leader_text = "**Leitung:** " + ", ".join(leader_names)
            else:
                leader_text = "**Leitung:** (Server nicht erreichbar)"

        embed = discord.Embed(
            title=f"📰 Neue Meldung: {faction_data.get('display_name', faction)}" if faction_data.get('type') == 'legal' else f"📜 Gerücht: {faction_data.get('display_name', faction)}",
            description=text,
            color=discord.Color.green() if faction_data.get("type") == "legal" else discord.Color.dark_grey(),
            timestamp=self.get_berlin_time()
        )
        embed.set_footer(text=f"Von {ctx.author.name}")
        if leader_text:
            embed.add_field(name="Verantwortliche", value=leader_text, inline=False)

        await target_channel.send(embed=embed)
        await ctx.send(f"✅ Meldung in {target_channel.mention} veröffentlicht.")

    @faction_group.command(name="changelog")
    async def faction_changelog(self, ctx, faction: str, *, text: str):
        factions = await self.config.factions()
        if faction.lower() not in factions:
            return await ctx.send("❌ Fraktion existiert nicht.")
        faction_data = factions[faction.lower()]
        if not await self.is_faction_leader(ctx.author, faction_data):
            return await ctx.send("❌ Kein Leader dieser Fraktion.")

        channel_id = await self.config.guild(ctx.guild).changelog_channel()
        if not channel_id:
            return await ctx.send("❌ Kein Changelog-Channel eingerichtet.")
        target_channel = self.bot.get_channel(channel_id)
        if not target_channel:
            return await ctx.send("❌ Zielkanal nicht gefunden.")

        embed = discord.Embed(
            title=f"📝 Fraktions-Update: {faction_data.get('display_name', faction)}",
            description=text,
            color=discord.Color.gold(),
            timestamp=self.get_berlin_time()
        )
        embed.set_footer(text=f"Verkündet von {ctx.author.name}")
        await target_channel.send(embed=embed)
        await ctx.send("✅ Changelog gepostet.")


async def setup(bot):
    await bot.add_cog(Fraktion(bot))
