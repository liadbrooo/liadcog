import discord
from redbot.core import commands, Config
import asyncio
import re
import random
from datetime import datetime, timedelta, timezone

class GiveawaySystem(commands.Cog):
    """Das ultimative, moderne Giveaway System."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=987654321123456789)
        default_guild = {
            "giveaways": {}
        }
        self.config.register_guild(**default_guild)
        self.giveaway_task = bot.loop.create_task(self.giveaway_looper())

    def cog_unload(self):
        self.giveaway_task.cancel()

    async def giveaway_looper(self):
        """Background task der prüft ob Giveaways abgelaufen sind."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                for guild in self.bot.guilds:
                    async with self.config.guild(guild).giveaways() as giveaways:
                        to_delete = []
                        for msg_id, gw in giveaways.items():
                            if gw.get("ended", False):
                                continue
                            end_time = datetime.fromisoformat(gw["end_time"])
                            if datetime.now(timezone.utc) >= end_time:
                                channel = guild.get_channel(gw["channel_id"])
                                if channel:
                                    await self.end_giveaway(guild, channel, int(msg_id), gw)
                                to_delete.append(msg_id)
                        
                        for msg_id in to_delete:
                            giveaways[msg_id]["ended"] = True
            except Exception as e:
                print(f"Error in Giveaway Looper: {e}")
            
            await asyncio.sleep(10)

    def parse_time(self, time_str):
        time_regex = re.compile(r"(\d+)([smhd])")
        matches = time_regex.findall(time_str)
        if not matches: return None
        delta = timedelta()
        for value, unit in matches:
            value = int(value)
            if unit == 's': delta += timedelta(seconds=value)
            elif unit == 'm': delta += timedelta(minutes=value)
            elif unit == 'h': delta += timedelta(hours=value)
            elif unit == 'd': delta += timedelta(days=value)
        return delta

    def get_giveaway_view(self, participant_count=0, ended=False, required_role_id=None):
        view = discord.ui.View()
        if ended:
            button = discord.ui.Button(style=discord.ButtonStyle.danger, label=f"Beendet • {participant_count} Teilnehmer", disabled=True)
        else:
            label = f"🎉 Mitmachen! ({participant_count} Teilnehmer)"
            button = discord.ui.Button(style=discord.ButtonStyle.success, label=label, custom_id="giveaway_join")
            button.callback = self.button_callback
        view.add_item(button)
        return view

    async def create_giveaway_embed(self, guild, gw_data, is_ended=False):
        end_time = datetime.fromisoformat(gw_data["end_time"])
        unix_timestamp = int(end_time.timestamp())
        host = guild.get_member(gw_data["host_id"])
        host_mention = host.mention if host else f"User ID: {gw_data['host_id']}"

        req_role_text = "Keine"
        if gw_data.get("required_role_id"):
            role = guild.get_role(gw_data["required_role_id"])
            req_role_text = role.mention if role else "Gelöschte Rolle"
            
        blacklist_text = "Keine"
        bl_items = []
        for rid in gw_data.get("blacklisted_roles", []):
            role = guild.get_role(rid)
            if role: bl_items.append(role.mention)
        for uid in gw_data.get("blacklisted_users", []):
            bl_items.append(f"<@{uid}>")
        if bl_items: blacklist_text = ", ".join(bl_items)

        bonus_text = "Keine"
        if gw_data.get("bonus_role_id"):
            role = guild.get_role(gw_data["bonus_role_id"])
            bonus_text = f"{role.mention if role else 'Gelöschte Rolle'} (2x Chance)"

        sponsor_text = "Keiner"
        if gw_data.get("sponsor"):
            sponsor_text = gw_data["sponsor"]

        if not is_ended:
            embed = discord.Embed(
                title="🎉 GIVEAWAY 🎉",
                description=(
                    f"**Preis:** {gw_data['prize']}\n\n"
                    f"⏳ **Endet:** <t:{unix_timestamp}:R> (<t:{unix_timestamp}:f>)\n"
                    f"👑 **Veranstaltet von:** {host_mention}\n"
                    f"👥 **Teilnehmer:** {len(gw_data['participants'])}\n\n"
                    f"✅ **Benötigte Rolle:** {req_role_text}\n"
                    f"⭐ **Bonus:** {bonus_text}\n"
                    f"🚫 **Ausgeschlossen:** {blacklist_text}\n"
                    f"🤝 **Sponsor:** {sponsor_text}\n\n"
                    f"*Klicke unten auf den Button um teilzunehmen!*"
                ),
                color=discord.Color.gold(),
                timestamp=end_time
            )
            embed.set_footer(text=f"Giveaway ID: {gw_data['message_id']} • Klick = An/Abmeldung")
            if gw_data.get("image_url"):
                embed.set_image(url=gw_data["image_url"])
        else:
            winner_text = "Keine gültigen Teilnehmer!"
            if gw_data["winners"]:
                winner_mentions = ", ".join(f"<@{wid}>" for wid in gw_data["winners"])
                winner_text = f"{winner_mentions}\nGlückwunsch!"
            
            embed = discord.Embed(
                title="🎉 GIVEAWAY BEENDET 🎉",
                description=(
                    f"**Preis:** {gw_data['prize']}\n\n"
                    f"🏆 **Gewinner:** {winner_text}\n"
                    f"👑 **Veranstaltet von:** {host_mention}\n"
                    f"🤝 **Sponsor:** {sponsor_text}"
                ),
                color=discord.Color.red(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text=f"Giveaway ID: {gw_data['message_id']}")
            if gw_data.get("image_url"):
                embed.set_image(url=gw_data["image_url"])

        return embed

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.command(name="gstart")
    async def gstart(self, ctx):
        """Startet das interaktive Setup für ein Giveaway."""
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel
        
        # 1. Preis
        await ctx.send("🎉 **Giveaway Setup gestartet!**\nWie soll der **Preis** lauten? (Abbruch mit `cancel`)")
        try:
            prize_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            if prize_msg.content.lower() == "cancel": return await ctx.send("❌ Abgebrochen.")
            prize = prize_msg.content
        except asyncio.TimeoutError: return await ctx.send("❌ Zeit abgelaufen. Setup abgebrochen.")

        # 2. Zeit
        await ctx.send("⏳ Wie lange soll es laufen? (z.B. `1d`, `12h`, `30m`)")
        try:
            time_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            if time_msg.content.lower() == "cancel": return await ctx.send("❌ Abgebrochen.")
            delta = self.parse_time(time_msg.content)
            if not delta: return await ctx.send("❌ Ungültiges Zeitformat. Setup abgebrochen.")
        except asyncio.TimeoutError: return await ctx.send("❌ Zeit abgelaufen. Setup abgebrochen.")

        # 3. Gewinner
        await ctx.send("🏆 Wie viele **Gewinner**? (Nur Zahlen)")
        try:
            winners_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            if winners_msg.content.lower() == "cancel": return await ctx.send("❌ Abgebrochen.")
            winners_count = int(winners_msg.content)
            if winners_count < 1: return await ctx.send("❌ Mindestens 1 Gewinner. Setup abgebrochen.")
        except ValueError: return await ctx.send("❌ Das war keine Zahl. Setup abgebrochen.")
        except asyncio.TimeoutError: return await ctx.send("❌ Zeit abgelaufen. Setup abgebrochen.")

        # 4. Whitelist (Rolle)
        await ctx.send("✅ Welche **Rolle** wird benötigt? (Erwähne Rolle, oder schreibe `keine`)")
        try:
            role_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            if role_msg.content.lower() == "cancel": return await ctx.send("❌ Abgebrochen.")
            required_role_id = None
            if role_msg.content.lower() != "keine":
                if not role_msg.role_mentions: return await ctx.send("❌ Keine Rolle erwähnt. Setup abgebrochen.")
                required_role_id = role_msg.role_mentions[0].id
        except asyncio.TimeoutError: return await ctx.send("❌ Zeit abgelaufen. Setup abgebrochen.")

        # 5. Bonus Rolle (2x Chance)
        await ctx.send("⭐ Gibt es eine **Bonus-Rolle** für doppelte Gewinnchance? (Erwähne Rolle, oder `keine`)")
        try:
            bonus_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            if bonus_msg.content.lower() == "cancel": return await ctx.send("❌ Abgebrochen.")
            bonus_role_id = None
            if bonus_msg.content.lower() != "keine":
                if not bonus_msg.role_mentions: return await ctx.send("❌ Keine Rolle erwähnt. Setup abgebrochen.")
                bonus_role_id = bonus_msg.role_mentions[0].id
        except asyncio.TimeoutError: return await ctx.send("❌ Zeit abgelaufen. Setup abgebrochen.")

        # 6. Blacklist
        await ctx.send("🚫 **Rollen/User ausschließen**? (Erwähne sie, oder `keine`)")
        try:
            bl_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            if bl_msg.content.lower() == "cancel": return await ctx.send("❌ Abgebrochen.")
            bl_roles = [r.id for r in bl_msg.role_mentions]
            bl_users = [u.id for u in bl_msg.mentions]
        except asyncio.TimeoutError: return await ctx.send("❌ Zeit abgelaufen. Setup abgebrochen.")

        # 7. Sponsor
        await ctx.send("🤝 Gibt es einen **Sponsor**? (Erwähne User/Rolle oder schreibe Text, oder `keiner`)")
        try:
            s_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            if s_msg.content.lower() == "cancel": return await ctx.send("❌ Abgebrochen.")
            sponsor = s_msg.content if s_msg.content.lower() != "keiner" else None
        except asyncio.TimeoutError: return await ctx.send("❌ Zeit abgelaufen. Setup abgebrochen.")

        # 8. Bild URL
        await ctx.send("🖼️ Soll ein **Bild** ans Embed angehängt werden? (Sende einen Bild-Link, oder `nein`)")
        try:
            img_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            if img_msg.content.lower() == "cancel": return await ctx.send("❌ Abgebrochen.")
            image_url = img_msg.content if img_msg.content.lower() != "nein" and img_msg.attachments == [] else None
            if img_msg.attachments:
                image_url = img_msg.attachments[0].url
        except asyncio.TimeoutError: return await ctx.send("❌ Zeit abgelaufen. Setup abgebrochen.")

        # Vorschau generieren
        end_time = datetime.now(timezone.utc) + delta
        gw_data = {
            "prize": prize, "winners_count": winners_count, "end_time": end_time.isoformat(),
            "host_id": ctx.author.id, "channel_id": ctx.channel.id, "message_id": 9999, # Platzhalter
            "winners": [], "participants": [], "required_role_id": required_role_id,
            "bonus_role_id": bonus_role_id, "blacklisted_roles": bl_roles, "blacklisted_users": bl_users,
            "sponsor": sponsor, "image_url": image_url, "ended": False
        }

        embed = await self.create_giveaway_embed(ctx.guild, gw_data)
        view = discord.ui.View()
        btn_start = discord.ui.Button(label="✅ Starten", style=discord.ButtonStyle.success)
        btn_cancel = discord.ui.Button(label="❌ Abbrechen", style=discord.ButtonStyle.danger)
        
        async def start_cb(interaction):
            if interaction.user != ctx.author:
                return await interaction.response.send_message("Nur der Ersteller kann das bestätigen.", ephemeral=True)
            
            real_view = self.get_giveaway_view(0, required_role_id=required_role_id)
            real_msg = await ctx.send(embed=embed, view=real_view)
            gw_data["message_id"] = real_msg.id
            
            async with self.config.guild(ctx.guild).giveaways() as giveaways:
                giveaways[str(real_msg.id)] = gw_data
            
            await interaction.message.edit(content="✅ **Giveaway erfolgreich gestartet!**", embed=None, view=None)
        
        async def cancel_cb(interaction):
            if interaction.user != ctx.author:
                return await interaction.response.send_message("Nur der Ersteller kann das abbrechen.", ephemeral=True)
            await interaction.message.edit(content="❌ **Abgebrochen.**", embed=None, view=None)

        btn_start.callback = start_cb
        btn_cancel.callback = cancel_cb
        view.add_item(btn_start)
        view.add_item(btn_cancel)

        await ctx.send("**📋 Vorschau:** Sieht das gut aus?", embed=embed, view=view)

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.command(name="gend")
    async def gend(self, ctx, message_id: int):
        """Beendet ein Giveaway sofort."""
        async with self.config.guild(ctx.guild).giveaways() as giveaways:
            if str(message_id) not in giveaways: return await ctx.send("❌ Giveaway nicht gefunden.")
            gw_data = giveaways[str(message_id)]
            if gw_data.get("ended"): return await ctx.send("❌ Already beendet.")
            channel = ctx.guild.get_channel(gw_data["channel_id"])
            if channel: await self.end_giveaway(ctx.guild, channel, message_id, gw_data)
            gw_data["ended"] = True
            await ctx.send("✅ Beendet.", delete_after=5)

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.command(name="gdelete")
    async def gdelete(self, ctx, message_id: int):
        """Löscht ein Giveaway komplett (ohne Gewinner zu ziehen)."""
        async with self.config.guild(ctx.guild).giveaways() as giveaways:
            if str(message_id) not in giveaways: return await ctx.send("❌ Nicht gefunden.")
            
            channel_id = giveaways[str(message_id)]["channel_id"]
            del giveaways[str(message_id)]
            
            channel = ctx.guild.get_channel(channel_id)
            if channel:
                try:
                    msg = await channel.fetch_message(message_id)
                    await msg.delete()
                except: pass
            
            await ctx.send("✅ Giveaway gelöscht.", delete_after=5)

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.command(name="glist")
    async def glist(self, ctx):
        """Zeigt alle aktiven Giveaways an."""
        async with self.config.guild(ctx.guild).giveaways() as giveaways:
            active = [gw for gw in giveaways.values() if not gw.get("ended", False)]
            if not active: return await ctx.send("Aktuell gibt es keine laufenden Giveaways.")
            
            embed = discord.Embed(title="🎉 Aktive Giveaways", color=discord.Color.gold())
            for gw in active[:10]: # Max 10 anzeigen
                end_time = datetime.fromisoformat(gw["end_time"])
                unix_ts = int(end_time.timestamp())
                embed.add_field(
                    name=f"🎁 {gw['prize']}",
                    value=f"**ID:** [{gw['message_id']}](https://discord.com/channels/{ctx.guild.id}/{gw['channel_id']}/{gw['message_id']})\n**Endet:** <t:{unix_ts}:R>\n**Teilnehmer:** {len(gw['participants'])}",
                    inline=False
                )
            await ctx.send(embed=embed)

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.command(name="greroll")
    async def greroll(self, ctx, message_id: int, channel: discord.TextChannel = None):
        """Zieht neue Gewinner."""
        target_channel = channel or ctx.channel
        async with self.config.guild(ctx.guild).giveaways() as giveaways:
            if str(message_id) not in giveaways: return await ctx.send("❌ Nicht gefunden.")
            gw_data = giveaways[str(message_id)]
            if not gw_data.get("ended"): return await ctx.send("❌ Muss erst beendet sein!")
            
            participants = gw_data["participants"]
            if not participants: return await ctx.send("❌ Keine Teilnehmer.")

            # Mit Bonus-Logik rerollen
            bonus_role_id = gw_data.get("bonus_role_id")
            pool = []
            for uid in participants:
                member = ctx.guild.get_member(uid)
                if member and bonus_role_id and any(r.id == bonus_role_id for r in member.roles):
                    pool.extend([uid, uid])
                else:
                    pool.append(uid)

            num_winners = min(gw_data["winners_count"], len(participants))
            new_winners = []
            for _ in range(num_winners):
                if not pool: break
                w = random.choice(pool)
                new_winners.append(w)
                pool = [x for x in pool if x != w] # Duplikate des Gewinners entfernen

            winner_mentions = ", ".join(f"<@{wid}>" for wid in new_winners)
            try:
                old_msg = await target_channel.fetch_message(message_id)
                old_embed = old_msg.embeds[0] if old_msg.embeds else None
                if old_embed:
                    old_embed.color = discord.Color.orange()
                    old_embed.add_field(name="🔄 Rerolled", value=f"Neue Gewinner: {winner_mentions}", inline=False)
                    await old_msg.edit(embed=old_embed, view=self.get_giveaway_view(len(participants), ended=True))
            except: pass
            await ctx.send(f"🔄 **Reroll!** Neue Gewinner: {winner_mentions}!")

    async def end_giveaway(self, guild, channel, message_id, gw_data):
        try:
            msg = await channel.fetch_message(message_id)
        except: return

        participants = gw_data["participants"]
        num_winners = min(gw_data["winners_count"], len(participants))
        winners = []
        
        if num_winners > 0:
            # Bonus Logik für doppelte Chance
            bonus_role_id = gw_data.get("bonus_role_id")
            pool = []
            for uid in participants:
                member = guild.get_member(uid)
                if member and bonus_role_id and any(r.id == bonus_role_id for r in member.roles):
                    pool.extend([uid, uid]) # 2x in den Topf
                else:
                    pool.append(uid)

            # Ziehen ohne Duplikate
            for _ in range(num_winners):
                if not pool: break
                w = random.choice(pool)
                winners.append(w)
                pool = [x for x in pool if x != w] # Alle Instanzen des Gewinners entfernen

        gw_data["winners"] = winners
        embed = await self.create_giveaway_embed(guild, gw_data, is_ended=True)
        view = self.get_giveaway_view(len(participants), ended=True)
        await msg.edit(embed=embed, view=view)
        
        if winners:
            winner_mentions = " ".join(f"<@{wid}>" for wid in winners)
            await channel.send(f"🎉 Glückwunsch {winner_mentions}! Du/Du habt **{gw_data['prize']}** gewonnen!")
            for wid in winners:
                user = self.bot.get_user(wid) or await self.bot.fetch_user(wid)
                if user:
                    try:
                        dm_embed = discord.Embed(
                            title="🎉 Giveaway Gewonnen!",
                            description=f"Herzlichen Glückwunsch! Du hast auf **{guild.name}** gewonnen!\n\n**Preis:** {gw_data['prize']}\nMelde dich bei einem Admin/Event-Manager.",
                            color=discord.Color.gold()
                        )
                        if gw_data.get("image_url"): dm_embed.set_image(url=gw_data["image_url"])
                        await user.send(embed=dm_embed)
                    except: pass 
        else:
            await channel.send(f"😢 Das Giveaway für **{gw_data['prize']}** wurde beendet, aber es gab keine gültigen Teilnehmer.")

    async def button_callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild: return

        async with self.config.guild(guild).giveaways() as giveaways:
            msg_id_str = str(interaction.message.id)
            if msg_id_str not in giveaways: return await interaction.response.send_message("Existiert nicht mehr.", ephemeral=True)
            gw = giveaways[msg_id_str]
            if gw.get("ended"): return await interaction.response.send_message("Bereits beendet!", ephemeral=True)

            member = interaction.user
            user_id = member.id

            # Checks
            if gw.get("required_role_id"):
                if not any(r.id == gw_data["required_role_id"] for r in member.roles):
                    role = guild.get_role(gw["required_role_id"])
                    return await interaction.response.send_message(f"❌ Du brauchst die Rolle **{role.name if role else 'Unbekannt'}**!", ephemeral=True)
            
            if any(r.id in gw.get("blacklisted_roles", []) for r in member.roles):
                return await interaction.response.send_message("🚫 Du hast eine ausgeschlossene Rolle!", ephemeral=True)
            if user_id in gw.get("blacklisted_users", []):
                return await interaction.response.send_message("🚫 Du wurdest ausgeschlossen!", ephemeral=True)

            if user_id in gw["participants"]:
                gw["participants"].remove(user_id)
                joined = False
            else:
                gw["participants"].append(user_id)
                joined = True
            
            participant_count = len(gw["participants"])

        embed = await self.create_giveaway_embed(guild, gw)
        view = self.get_giveaway_view(participant_count, required_role_id=gw.get("required_role_id"))
        await interaction.response.edit_message(embed=embed, view=view)
        
        if joined:
            msg = "✅ Du nimmst teil!"
            if gw.get("bonus_role_id") and any(r.id == gw["bonus_role_id"] for r in member.roles):
                msg += " (⭐ Bonus-Chance aktiv!)"
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.followup.send("❌ Teilnahme zurückgezogen.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(GiveawaySystem(bot))
