import discord
from redbot.core import commands, Config
from redbot.core.utils.chat_formatting import pagify
from datetime import datetime
import uuid

class Fraktion(commands.Cog):
    """Fraktionsverwaltung für FiveM DE-RP Server (Haupt- und Fraktions-Discords)"""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        
        default_global = {
            "factions": {},
            "legal_board_channel": None,      # Stadtblatt Channel ID
            "illegal_board_channel": None,    # Schwarzes Brett Channel ID
            "changelog_channel": None         # Globaler Fraktions-Changelog Channel ID
        }
        default_user = {
            "blacklists": [] # Speichert fraktionsbezogene Banns: [{"faction": "lspd", "reason": "...", "mod": "...", "date": "..."}]
        }
        
        self.config.register_global(**default_global)
        self.config.register_user(**default_user)

    # --- HELPER METHODEN ---
    
    def is_faction_leader(self, user: discord.User, faction_data: dict) -> bool:
        """Prüft, ob der User eine Leader-Rolle der Fraktion hat."""
        guild = self.bot.get_guild(faction_data["guild_id"])
        if not guild:
            return False
        member = guild.get_member(user.id)
        if not member:
            return False
        return any(role.id in faction_data["leader_role_ids"] for role in member.roles)

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
        """Setzt den Channel für das Stadtblatt (Legale Fraktionen)."""
        await self.config.legal_board_channel.set(channel.id)
        await ctx.send(f"✅ Das Stadtblatt wurde auf {channel.mention} gesetzt.")

    @faction_setup.command(name="schwarzesbrett")
    async def setup_schwarzesbrett(self, ctx, channel: discord.TextChannel):
        """Setzt den Channel für das Schwarze Brett (Illegale Fraktionen)."""
        await self.config.illegal_board_channel.set(channel.id)
        await ctx.send(f"✅ Das Schwarze Brett wurde auf {channel.mention} gesetzt.")

    @faction_setup.command(name="changelog")
    async def setup_changelog(self, ctx, channel: discord.TextChannel):
        """Setzt den Channel für fraktionsinterne Changelogs."""
        await self.config.changelog_channel.set(channel.id)
        await ctx.send(f"✅ Der Fraktions-Changelog-Channel wurde auf {channel.mention} gesetzt.")

    # --- FRAKTIONSVERWALTUNG ---

    @faction_group.command(name="add")
    @commands.admin_or_permissions(manage_guild=True)
    async def faction_add(self, ctx, name: str, typ: str, guild_id: int, *, leader_role_ids: str):
        """Fügt eine neue Fraktion hinzu.
        
        Args:
            name: Kurzname der Fraktion (z.B. LSPD)
            typ: "legal" oder "illegal"
            guild_id: Die Discord-Server-ID des Fraktions-Discords
            leader_role_ids: Kommagetrennte Liste der Leader-Rollen-IDs (z.B. 123, 456)
        """
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
        """Entfernt eine Fraktion aus dem System."""
        async with self.config.factions() as factions:
            if name.lower() not in factions:
                return await ctx.send("❌ Diese Fraktion existiert nicht.")
            del factions[name.lower()]
        await ctx.send(f"✅ Fraktion **{name}** wurde entfernt.")

    @faction_group.command(name="list", aliases=["liste"])
    async def faction_list(self, ctx):
        """Zeigt alle registrierten Fraktionen an."""
        factions = await self.config.factions()
        if not factions:
            return await ctx.send("Aktuell sind keine Fraktionen registriert.")
            
        embed = discord.Embed(title="🚓 Fraktionsliste", color=discord.Color.blue())
        msg = ""
        for key, data in factions.items():
            guild = self.bot.get_guild(data["guild_id"])
            guild_name = guild.name if guild else "Unbekannt"
            emoji = "🛡️" if data["type"] == "legal" else "💀"
            msg += f"{emoji} **{data['display_name']}** ({data['type']})\n"
            msg += f"└ Server: `{guild_name}`\n"
            msg += f"└ Verwarnungen: `{len(data.get('warnings', []))}`\n\n"
            
        for page in pagify(msg, page_length=1024):
            embed.add_field(name="\u200b", value=page, inline=False)
        await ctx.send(embed=embed)

    # --- VERWARNUNGEN (STRIKES) ---

    @faction_group.command(name="warn", aliases=["verwarnung"])
    @commands.admin_or_permissions(manage_messages=True)
    async def faction_warn(self, ctx, faction: str, *, reason: str):
        """Gibt einer gesamten Fraktion eine Verwarnung (Strike)."""
        factions = await self.config.factions()
        if faction.lower() not in factions:
            return await ctx.send("❌ Diese Fraktion existiert nicht.")
            
        faction_data = factions[faction.lower()]
        warning_id = str(uuid.uuid4())[:8]
        current_time = datetime.now().strftime("%d.%m.%Y %H:%M")
        
        guild = self.bot.get_guild(faction_data["guild_id"])
        notified_leaders = []
        if guild:
            for member in guild.members:
                if any(role.id in faction_data["leader_role_ids"] for role in member.roles):
                    notified_leaders.append(member.mention)
                    try:
                        dm_embed = discord.Embed(title=f"⚠️ Fraktionsverwarnung für {faction_data['display_name']}", color=discord.Color.red())
                        dm_embed.add_field(name="Grund", value=reason, inline=False)
                        dm_embed.set_footer(text=f"Ausgestellt von {ctx.author.name} am {current_time}")
                        await member.send(embed=dm_embed)
                    except discord.Forbidden:
                        pass
        
        async with self.config.factions() as f:
            f[faction.lower()]["warnings"].append({
                "id": warning_id, "reason": reason, "moderator": ctx.author.name, "date": current_time
            })
            
        embed = discord.Embed(title="⚠️ Fraktionsverwarnung ausgesprochen", color=discord.Color.red())
        embed.add_field(name="Fraktion", value=faction_data["display_name"], inline=True)
        embed.add_field(name="Warn-ID", value=warning_id, inline=True)
        embed.add_field(name="Grund", value=reason, inline=False)
        embed.add_field(name="Benachrichtigte Leader", value=", ".join(notified_leaders) if notified_leaders else "Keine gefunden", inline=False)
        await ctx.send(embed=embed)

    @faction_group.command(name="warns", aliases=["akte"])
    async def faction_warns(self, ctx, faction: str):
        """Zeigt alle Verwarnungen einer Fraktion an."""
        factions = await self.config.factions()
        if faction.lower() not in factions:
            return await ctx.send("❌ Diese Fraktion existiert nicht.")
            
        warnings = factions[faction.lower()].get("warnings", [])
        if not warnings:
            return await ctx.send(f"✅ Die Fraktion **{faction}** hat keine Verwarnungen.")
            
        embed = discord.Embed(title=f"Fraktionsakte: {faction}", description=f"Insgesamt {len(warnings)} Verwarnung(en).", color=discord.Color.orange())
        msg = ""
        for w in warnings:
            msg += f"**ID:** `{w['id']}` | **Grund:** {w['reason']}\n**Von:** {w['moderator']} am {w['date']}\n------------------------\n"
        for page in pagify(msg, page_length=1024):
            embed.add_field(name="\u200b", value=page, inline=False)
        await ctx.send(embed=embed)

    # --- BLACKLIST SYSTEM (Globale Server-Akte) ---

    @faction_group.group(name="blacklist", aliases=["bl"])
    @commands.admin_or_permissions(manage_messages=True)
    async def faction_blacklist(self, ctx):
        """Verwaltung von Fraktions-Blacklists (Hausverbot/V-Mann Bann)."""
        pass

    @faction_blacklist.command(name="add")
    async def bl_add(self, ctx, user: discord.User, faction: str, *, reason: str):
        """Setzt einen User auf die Blacklist einer Fraktion."""
        factions = await self.config.factions()
        if faction.lower() not in factions:
            return await ctx.send("❌ Diese Fraktion existiert nicht.")
            
        current_time = datetime.now().strftime("%d.%m.%Y")
        async with self.config.user(user).blacklists() as blacklists:
            # Prüfen ob schon auf dieser Fraktion gebannt
            if any(b["faction"].lower() == faction.lower() for b in blacklists):
                return await ctx.send(f"❌ {user.name} ist bereits auf der Blacklist von {faction}.")
            blacklists.append({
                "faction": factions[faction.lower()]["display_name"],
                "reason": reason, "moderator": ctx.author.name, "date": current_time
            })
            
        await ctx.send(f"🚫 {user.mention} wurde auf die Blacklist von **{faction}** gesetzt.\n**Grund:** {reason}")

    @faction_blacklist.command(name="remove")
    async def bl_remove(self, ctx, user: discord.User, faction: str):
        """Entfernt einen User von der Fraktions-Blacklist."""
        async with self.config.user(user).blacklists() as blacklists:
            initial_len = len(blacklists)
            blacklists[:] = [b for b in blacklists if b["faction"].lower() != faction.lower()]
            if len(blacklists) == initial_len:
                return await ctx.send("❌ Eintrag nicht gefunden.")
                
        await ctx.send(f"✅ {user.name} wurde von der Blacklist der Fraktion **{faction}** entfernt.")

    @faction_blacklist.command(name="check", aliases=["info"])
    async def bl_check(self, ctx, user: discord.User):
        """Prüft, auf welchen Fraktions-Blacklists ein User steht."""
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
        """Postet eine öffentliche RP-Meldung ins Stadtblatt oder ans Schwarze Brett."""
        factions = await self.config.factions()
        if faction.lower() not in factions:
            return await ctx.send("❌ Diese Fraktion existiert nicht.")
            
        faction_data = factions[faction.lower()]
        if not self.is_faction_leader(ctx.author, faction_data):
            return await ctx.send("❌ Du bist kein Leader dieser Fraktion und darfst keine Meldungen absetzen.")
            
        channel_id = await self.config.legal_board_channel() if faction_data["type"] == "legal" else await self.config.illegal_board_channel()
        if not channel_id:
            return await ctx.send("❌ Es wurde kein Channel für das Stadtblatt / Schwarze Brett eingerichtet. Ein Admin muss erst `[p]fk setup` machen.")
            
        target_channel = self.bot.get_channel(channel_id)
        if not target_channel:
            return await ctx.send("❌ Der konfigurierte Channel konnte nicht gefunden werden.")
            
        embed = discord.Embed(
            title=f"📰 Neue Meldung: {faction_data['display_name']}" if faction_data['type'] == 'legal' else f"📜 Gerücht aus der Unterwelt: {faction_data['display_name']}",
            description=text,
            color=discord.Color.green() if faction_data['type'] == 'legal' else discord.Color.dark_grey(),
            timestamp=datetime.now()
        )
        embed.set_footer(text=f"Gezeichnet von {ctx.author.name}")
        
        await target_channel.send(embed=embed)
        await ctx.send(f"✅ Deine Meldung wurde im {target_channel.mention} veröffentlicht.")

    @faction_group.command(name="changelog")
    async def faction_changelog(self, ctx, faction: str, *, text: str):
        """Postet ein fraktionsinternes Update in den Changelog-Channel."""
        factions = await self.config.factions()
        if faction.lower() not in factions:
            return await ctx.send("❌ Diese Fraktion existiert nicht.")
            
        faction_data = factions[faction.lower()]
        if not self.is_faction_leader(ctx.author, faction_data):
            return await ctx.send("❌ Du bist kein Leader dieser Fraktion.")
            
        channel_id = await self.config.changelog_channel()
        if not channel_id:
            return await ctx.send("❌ Es wurde kein Changelog-Channel eingerichtet. Ein Admin muss erst `[p]fk setup changelog` machen.")
            
        target_channel = self.bot.get_channel(channel_id)
        if not target_channel:
            return await ctx.send("❌ Der konfigurierte Channel konnte nicht gefunden werden.")
            
        embed = discord.Embed(
            title=f"📝 Fraktions-Update: {faction_data['display_name']}",
            description=text,
            color=discord.Color.gold()
        )
        embed.set_footer(text=f"Verkündet von {ctx.author.name}")
        
        await target_channel.send(embed=embed)
        await ctx.send("✅ Dein Changelog-Eintrag wurde gepostet.")
