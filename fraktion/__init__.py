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
            "factions": {},
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
        self.config.register_user(**default_user)

    # --- HELPER METHODEN ---

    def get_berlin_time(self):
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo("Europe/Berlin"))
        except Exception:
            from datetime import timezone
            return datetime.now(timezone(timedelta(hours=2)))

    def is_faction_leader(self, user: discord.User, faction_data: dict) -> bool:
        guild = self.bot.get_guild(faction_data.get("guild_id", 0))
        if not guild:
            return False
        member = guild.get_member(user.id)
        if not member:
            return False
        leader_ids = faction_data.get("leader_role_ids", [])
        return any(role.id in leader_ids for role in member.roles)

    def parse_duration(self, duration_str: str):
        if duration_str.lower() in ["perm", "permanent", "0"]:
            return None
        match = re.match(r"(\d+)([dhmw])", duration_str.lower())
        if not match:
            return False 
        amount = int(match.group(1))
        unit = match.group(2)
        if unit == "m": delta = timedelta(minutes=amount)
        elif unit == "h": delta = timedelta(hours=amount)
        elif unit == "d": delta = timedelta(days=amount)
        elif unit == "w": delta = timedelta(weeks=amount)
        
        berlin_now = self.get_berlin_time()
        return berlin_now + delta

    # --- CUSTOM CHECKS ---

    def is_warn_authorized():
        async def predicate(ctx):
            if ctx.author.guild_permissions.manage_guild:
                return True
            
            authorized_roles = await ctx.cog.config.warn_roles()
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
        """Befehle für die Fraktionsverwaltung."""
        pass

    @faction_group.group(name="setup")
    @commands.admin_or_permissions(manage_guild=True)
    async def faction_setup(self, ctx):
        """Einstellungen für das Fraktionssystem treffen."""
        pass

    @faction_setup.command(name="stadtblatt")
    async def setup_stadtblatt(self, ctx, channel: discord.TextChannel):
        await self.config.legal_board_channel.set(channel.id)
        await ctx.send(f"✅ Das Stadtblatt wurde auf {channel.mention} gesetzt.")

    @faction_setup.command(name="schwarzesbrett")
    async def setup_schwarzesbrett(self, ctx, channel: discord.TextChannel):
        await self.config.illegal_board_channel.set(channel.id)
        await ctx.send(f"✅ Das Schwarze Brett wurde auf {channel.mention} gesetzt.")

    @faction_setup.command(name="changelog")
    async def setup_changelog(self, ctx, channel: discord.TextChannel):
        await self.config.changelog_channel.set(channel.id)
        await ctx.send(f"✅ Der Fraktions-Changelog-Channel wurde auf {channel.mention} gesetzt.")

    @faction_setup.command(name="warnlog")
    async def setup_warnlog(self, ctx, channel: discord.TextChannel):
        await self.config.warn_log_channel.set(channel.id)
        await ctx.send(f"✅ Der Warn-Log-Channel wurde auf {channel.mention} gesetzt.")

    @faction_setup.command(name="warnroles", aliases=["warnrollen"])
    async def setup_warnroles(self, ctx, roles: commands.Greedy[discord.Role]):
        """Legt die Rollen fest, die Fraktionsverwarnungen aussprechen dürfen.
        
        Beispiel: [p]fk setup warnroles @Support @Leitung
        """
        if not roles:
            await self.config.warn_roles.set([])
            return await ctx.send("✅ Die autorisierten Rollen wurden zurückgesetzt. Ab sofort greift wieder die Standard-Berechtigung (Nachrichten verwalten).")
            
        role_ids = [r.id for r in roles]
        await self.config.warn_roles.set(role_ids)
        await ctx.send(f"✅ Folgende Rollen dürfen ab sofort Fraktionsverwarnungen aussprechen:\n{', '.join(r.mention for r in roles)}")

    # --- FRAKTIONSVERWALTUNG ---

    @faction_group.command(name="add")
    @commands.admin_or_permissions(manage_guild=True)
    async def faction_add(self, ctx, name: str, typ: str, guild_id: int, *, leader_role_ids: str):
        try:
            role_ids = [int(r.strip()) for r in leader_role_ids.split(",")]
        except ValueError:
            return await ctx.send("❌ Fehler beim Parsen der Rollen-IDs. Bitte im Format: `123, 456, 789`")

        if typ.lower() not in ["legal", "illegal"]:
            return await ctx.send("❌ Der Typ muss entweder `legal` oder `illegal` sein.")

        async with self.config.factions() as factions:
            if name.lower() in factions:
                return await ctx.send("❌ Diese Fraktion existiert bereits!")
            factions[name.lower()] = {
                "display_name": name,
                "type": typ.lower(),
                "guild_id": guild_id,
                "leader_role_ids": role_ids,
                "warnings": []
            }
        await ctx.send(f"✅ Fraktion **{name}** ({typ}) wurde erfolgreich hinzugefügt!")

    @faction_group.command(name="remove")
    @commands.admin_or_permissions(manage_guild=True)
    async def faction_remove(self, ctx, name: str):
        async with self.config.factions() as factions:
            if name.lower() not in factions:
                return await ctx.send("❌ Diese Fraktion existiert nicht.")
            del factions[name.lower()]
        await ctx.send(f"✅ Fraktion **{name}** wurde entfernt.")

    @faction_group.command(name="list", aliases=["liste"])
    async def faction_list(self, ctx):
        """Zeigt alle Fraktionen inkl. aktueller Leitung an."""
        factions = await self.config.factions()
        if not factions:
            return await ctx.send("Aktuell sind keine Fraktionen registriert.")
            
        # Discord erlaubt max 25 Felder pro Embed. Wir teilen die Liste in 25er Blöcke auf.
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
                embed.set_footer(text=f"Insgesamt {len(factions)} Fraktionen registriert.")
                
            for key, data in chunk:
                guild = self.bot.get_guild(data.get("guild_id", 0))
                emoji = "🛡️" if data.get("type", "legal") == "legal" else "💀"
                name = data.get('display_name', key)
                
                desc = ""
                if guild:
                    leaders = []
                    leader_role_names = []
                    leader_ids = data.get("leader_role_ids", [])
                    for member in guild.members:
                        member_leader_roles = [r for r in member.roles if r.id in leader_ids]
                        if member_leader_roles:
                            leaders.append(member.mention)
                            for r in member_leader_roles:
                                if r.name not in leader_role_names:
                                    leader_role_names.append(r.name)
                    
                    desc += f"**Server:** `{guild.name}`\n"
                    if leaders:
                        desc += f"**Leitung** ({', '.join(leader_role_names)}):\n{', '.join(leaders)}\n"
                    else:
                        desc += "**Leitung:** Keine Leader online/gefunden\n"
                else:
                    desc += f"**Server:** `Nicht gefunden (ID: {data.get('guild_id', 0)})`\n"
                    desc += "**Leitung:** `Bot ist nicht auf dem Server`\n"
                    
                warns = len(data.get('warnings', []))
                desc += f"**Verwarnungen:** `{warns}`"
                
                embed.add_field(name=f"{emoji} {name}", value=desc, inline=False)
                
            pages.append(embed)
            
        for page in pages:
            await ctx.send(embed=page)

    # --- VERWARNUNGEN (STRIKES) ---

    @faction_group.command(name="warn", aliases=["verwarnung"])
    @is_warn_authorized()
    async def faction_warn(self, ctx, faction: str, *, reason_text: str):
        try:
            factions = await self.config.factions()
            if faction.lower() not in factions:
                return await ctx.send("❌ Diese Fraktion existiert nicht.")
                
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
                return await ctx.send("❌ Du musst einen Grund angeben.")
                
            expires_at = self.parse_duration(duration)
                
            warning_id = str(uuid.uuid4())[:8]
            current_time = self.get_berlin_time()
            current_time_str = current_time.strftime("%d.%m.%Y %H:%M")
            
            if expires_at:
                expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
            else:
                expires_str = "Permanent"
            
            guild = self.bot.get_guild(faction_data.get("guild_id", 0))
            notified_leaders = []
            leader_ids = faction_data.get("leader_role_ids", [])
            
            if guild and leader_ids:
                for member in guild.members:
                    if any(role.id in leader_ids for role in member.roles):
                        notified_leaders.append(member.mention)
                        try:
                            dm_embed = discord.Embed(
                                title="⚠️ Amtliche Fraktionsverwarnung",
                                description=f"Eure Fraktion **{faction_data.get('display_name', faction)}** hat eine Verwarnung erhalten.",
                                color=discord.Color.red(),
                                timestamp=current_time
                            )
                            dm_embed.add_field(name="🆔 Verwarnungs-ID", value=f"`{warning_id}`", inline=True)
                            dm_embed.add_field(name="⏳ Dauer", value=f"`{expires_str}`", inline=True)
                            dm_embed.add_field(name="📝 Begründung", value=reason, inline=False)
                            dm_embed.set_footer(text=f"Ausgestellt durch: {ctx.author.name}")
                            await member.send(embed=dm_embed)
                        except Exception:
                            pass
            
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
            
            leaders_str = ", ".join(notified_leaders) if notified_leaders else "Keine Leader gefunden"
            if len(leaders_str) > 1024: leaders_str = f"{len(notified_leaders)} Leader wurden per DM benachrichtigt."
            embed.add_field(name="Benachrichtigte Leader", value=leaders_str, inline=False)
            
            await ctx.send(embed=embed)
            
            log_channel_id = await self.config.warn_log_channel()
            if log_channel_id:
                log_channel = self.bot.get_channel(log_channel_id)
                if log_channel:
                    log_embed = discord.Embed(title="🚨 Neue Fraktionsverwarnung", color=discord.Color.dark_red(), timestamp=current_time)
                    log_embed.add_field(name="Fraktion", value=faction_data.get('display_name', faction), inline=True)
                    log_embed.add_field(name="Warn-ID", value=warning_id, inline=True)
                    log_embed.add_field(name="Dauer", value=expires_str, inline=True)
                    log_embed.add_field(name="Grund", value=reason, inline=False)
                    log_embed.set_footer(text=f"Ausgestellt von {ctx.author.name}")
                    try:
                        await log_channel.send(embed=log_embed)
                    except:
                        pass

        except Exception as e:
            error_msg = f"```py\n{traceback.format_exc()}\n```"
            if len(error_msg) > 1900:
                error_msg = error_msg[-1900:]
            await ctx.send(f"❌ Ein interner Fehler ist aufgetreten. Bitte sende diesen Text an den Entwickler:\n{error_msg}")

    @faction_group.command(name="warns", aliases=["akte"])
    async def faction_warns(self, ctx, faction: str):
        factions = await self.config.factions()
        if faction.lower() not in factions:
            return await ctx.send("❌ Diese Fraktion existiert nicht.")
            
        warnings = factions[faction.lower()].get("warnings", [])
        if not warnings:
            return await ctx.send(f"✅ Die Fraktion **{faction}** hat keine Verwarnungen.")
            
        embed = discord.Embed(title=f"Fraktionsakte: {faction}", description=f"Insgesamt {len(warnings)} Eintrag(en).", color=discord.Color.orange())
        msg = ""
        
        now = datetime.now().timestamp()
        for w in warnings:
            status = "**[ABGELAUFEN]**" if w.get("expires") and w["expires"] < now else "**[AKTIV]**"
            expires_str = datetime.fromtimestamp(w["expires"]).strftime("%d.%m.%Y %H:%M") if w.get("expires") else "Permanent"
            
            msg += f"{status} **ID:** `{w['id']}`\n"
            msg += f"**Grund:** {w['reason']}\n"
            msg += f"**Dauer:** Läuft ab am {expires_str}\n" if w.get("expires") else "**Dauer:** Permanent\n"
            msg += f"**Von:** {w['moderator']} am {w['date']}\n------------------------\n"
            
        for page in pagify(msg, page_length=1024):
            embed.add_field(name="\u200b", value=page, inline=False)
        await ctx.send(embed=embed)

    @faction_group.command(name="unwarn", aliases=["removewarn"])
    @commands.admin_or_permissions(manage_guild=True)
    async def faction_unwarn(self, ctx, faction: str, warning_id: str):
        async with self.config.factions() as f:
            if faction.lower() not in f:
                return await ctx.send("❌ Diese Fraktion existiert nicht.")
                
            if "warnings" not in f[faction.lower()]:
                return await ctx.send("❌ Diese Fraktion hat keine Verwarnungen.")
                
            warnings = f[faction.lower()]["warnings"]
            initial_len = len(warnings)
            warnings[:] = [w for w in warnings if w["id"] != warning_id]
            
            if len(warnings) == initial_len:
                return await ctx.send("❌ Keine Verwarnung mit dieser ID bei dieser Fraktion gefunden.")
                
        await ctx.send(f"✅ Verwarnung `{warning_id}` wurde von der Fraktion **{faction}** entfernt.")

    # --- BLACKLIST SYSTEM ---

    @faction_group.group(name="blacklist", aliases=["bl"])
    @commands.admin_or_permissions(manage_messages=True)
    async def faction_blacklist(self, ctx):
        pass

    @faction_blacklist.command(name="add")
    async def bl_add(self, ctx, user: discord.User, faction: str, *, reason: str):
        factions = await self.config.factions()
        if faction.lower() not in factions:
            return await ctx.send("❌ Diese Fraktion existiert nicht.")
            
        current_time = self.get_berlin_time().strftime("%d.%m.%Y")
        async with self.config.user(user).blacklists() as blacklists:
            if any(b["faction"].lower() == faction.lower() for b in blacklists):
                return await ctx.send(f"❌ {user.name} ist bereits auf der Blacklist von {faction}.")
            blacklists.append({
                "faction": factions[faction.lower()].get("display_name", faction),
                "reason": reason, "moderator": ctx.author.name, "date": current_time
            })
        await ctx.send(f"🚫 {user.mention} wurde auf die Blacklist von **{faction}** gesetzt.\n**Grund:** {reason}")

    @faction_blacklist.command(name="remove")
    async def bl_remove(self, ctx, user: discord.User, faction: str):
        async with self.config.user(user).blacklists() as blacklists:
            initial_len = len(blacklists)
            blacklists[:] = [b for b in blacklists if b["faction"].lower() != faction.lower()]
            if len(blacklists) == initial_len:
                return await ctx.send("❌ Eintrag nicht gefunden.")
        await ctx.send(f"✅ {user.name} wurde von der Blacklist der Fraktion **{faction}** entfernt.")

    @faction_blacklist.command(name="check", aliases=["info"])
    async def bl_check(self, ctx, user: discord.User):
        blacklists = await self.config.user(user).blacklists()
        if not blacklists:
            return await ctx.send(f"✅ {user.name} steht auf keiner Fraktions-Blacklist.")
            
        embed = discord.Embed(title=f"🚫 Blacklist-Akte: {user.name}", color=discord.Color.dark_red())
        msg = ""
        for b in blacklists:
            msg += f"**Fraktion:** {b['faction']}\n**Grund:** {b['reason']}\n**Von:** {b['moderator']} am {b['date']}\n------------------------\n"
        for page in pagify(msg, page_length=1024):
            embed.add_field(name="\u200b", value=page, inline=False)
        await ctx.send(embed=embed)

    # --- ÖFFENTLICHE MELDUNGEN & CHANGELOG ---

    @faction_group.command(name="meldung")
    async def faction_meldung(self, ctx, faction: str, *, text: str):
        factions = await self.config.factions()
        if faction.lower() not in factions:
            return await ctx.send("❌ Diese Fraktion existiert nicht.")
            
        faction_data = factions[faction.lower()]
        if not self.is_faction_leader(ctx.author, faction_data):
            return await ctx.send("❌ Du bist kein Leader dieser Fraktion und darfst keine Meldungen absetzen.")
            
        channel_id = await self.config.legal_board_channel() if faction_data.get("type", "legal") == "legal" else await self.config.illegal_board_channel()
        if not channel_id:
            return await ctx.send("❌ Es wurde kein Channel für das Stadtblatt / Schwarze Brett eingerichtet.")
            
        target_channel = self.bot.get_channel(channel_id)
        if not target_channel:
            return await ctx.send("❌ Der konfigurierte Channel konnte nicht gefunden werden.")
            
        ping_roles = [f"<@&{rid}>" for rid in faction_data.get("leader_role_ids", [])]
        ping_str = " | ".join(ping_roles)
            
        embed = discord.Embed(
            title=f"📰 Neue Meldung: {faction_data.get('display_name', faction)}" if faction_data.get('type') == 'legal' else f"📜 Gerücht aus der Unterwelt: {faction_data.get('display_name', faction)}",
            description=text,
            color=discord.Color.green() if faction_data.get("type") == "legal" else discord.Color.dark_grey(),
            timestamp=self.get_berlin_time()
        )
        embed.set_footer(text=f"Gezeichnet von {ctx.author.name}")
        
        await target_channel.send(content=f"{ping_str}" if ping_str else None, embed=embed)
        await ctx.send(f"✅ Deine Meldung wurde im {target_channel.mention} veröffentlicht.")

    @faction_group.command(name="changelog")
    async def faction_changelog(self, ctx, faction: str, *, text: str):
        factions = await self.config.factions()
        if faction.lower() not in factions:
            return await ctx.send("❌ Diese Fraktion existiert nicht.")
            
        faction_data = factions[faction.lower()]
        if not self.is_faction_leader(ctx.author, faction_data):
            return await ctx.send("❌ Du bist kein Leader dieser Fraktion.")
            
        channel_id = await self.config.changelog_channel()
        if not channel_id:
            return await ctx.send("❌ Es wurde kein Changelog-Channel eingerichtet.")
            
        target_channel = self.bot.get_channel(channel_id)
        if not target_channel:
            return await ctx.send("❌ Der konfigurierte Channel konnte nicht gefunden werden.")
            
        embed = discord.Embed(
            title=f"📝 Fraktions-Update: {faction_data.get('display_name', faction)}",
            description=text,
            color=discord.Color.gold(),
            timestamp=self.get_berlin_time()
        )
        embed.set_footer(text=f"Verkündet von {ctx.author.name}")
        
        await target_channel.send(embed=embed)
        await ctx.send("✅ Dein Changelog-Eintrag wurde gepostet.")


async def setup(bot):
    await bot.add_cog(Fraktion(bot))
