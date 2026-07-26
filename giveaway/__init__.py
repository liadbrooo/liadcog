import discord
from redbot.core import commands, Config
import asyncio
import re
import random
from datetime import datetime, timedelta, timezone

class GiveawaySystem(commands.Cog):
    """Das ultimative, 100% stabile Giveaway System mit Reaktionen."""

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

    async def create_giveaway_embed(self, guild, gw_data, is_ended=False):
        end_time = datetime.fromisoformat(gw_data["end_time"])
        unix_timestamp = int(end_time.timestamp())
        host = guild.get_member(gw_data["host_id"])
        host_mention = host.mention if host else f"<@{gw_data['host_id']}>"

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
                    f"👑 **Veranstaltet von:** {host_mention}\n\n"
                    f"✅ **Benötigte Rolle:** {req_role_text}\n"
                    f"⭐ **Bonus:** {bonus_text}\n"
                    f"🚫 **Ausgeschlossen:** {blacklist_text}\n"
                    f"🤝 **Sponsor:** {sponsor_text}\n\n"
                    f"*Reagiere unten mit 🎉 um teilzunehmen!*"
                ),
                color=discord.Color.gold(),
                timestamp=end_time
            )
            embed.set_footer(text=f"Giveaway ID: {gw_data['message_id']}")
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
        
        # 1. Channel
        await ctx.send("📢 In welchen **Channel** soll das Giveaway? (Erwähne den #channel, oder schreibe `hier` für diesen Channel)")
        try:
            ch_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            if ch_msg.content.lower() == "cancel": return await ctx.send("❌ Abgebrochen.")
            if ch_msg.content.lower() == "hier":
                target_channel = ctx.channel
            elif ch_msg.channel_mentions:
                target_channel = ch_msg.channel_mentions[0]
            else:
                return await ctx.send("❌ Kein gültiger Channel. Setup abgebrochen.")
        except asyncio.TimeoutError: return await ctx.send("❌ Zeit abgelaufen. Setup abgebrochen.")

        # 2. Host / Veranstalter
        await ctx.send("👑 Wer **veranstaltet** das Giveaway? (Erwähne den User, oder schreibe `ich`)")
        try:
            host_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            if host_msg.content.lower() == "cancel": return await ctx.send("❌ Abgebrochen.")
            if host_msg.content.lower() == "ich":
                host_id = ctx.author.id
            elif host_msg.mentions:
                host_id = host_msg.mentions[0].id
            else:
                return await ctx.send("❌ Kein User erwähnt. Setup abgebrochen.")
        except asyncio.TimeoutError: return await ctx.send("❌ Zeit abgelaufen. Setup abgebrochen.")

        # 3. Preis
        await ctx.send("🎁 Wie soll der **Preis** lauten? (Abbruch mit `cancel`)")
        try:
            prize_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            if prize_msg.content.lower() == "cancel": return await ctx.send("❌ Abgebrochen.")
            prize = prize_msg.content
        except asyncio.TimeoutError: return await ctx.send("❌ Zeit abgelaufen. Setup abgebrochen.")

        # 4. Zeit
        await ctx.send("⏳ Wie lange soll es laufen? (z.B. `1d`, `12h`, `30m`)")
        try:
            time_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            if time_msg.content.lower() == "cancel": return await ctx.send("❌ Abgebrochen.")
            delta = self.parse_time(time_msg.content)
            if not delta: return await ctx.send("❌ Ungültiges Zeitformat. Setup abgebrochen.")
        except asyncio.TimeoutError: return await ctx.send("❌ Zeit abgelaufen. Setup abgebrochen.")

        # 5. Gewinner
        await ctx.send("🏆 Wie viele **Gewinner**? (Nur Zahlen)")
        try:
            winners_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            if winners_msg.content.lower() == "cancel": return await ctx.send("❌ Abgebrochen.")
            winners_count = int(winners_msg.content)
            if winners_count < 1: return await ctx.send("❌ Mindestens 1 Gewinner. Setup abgebrochen.")
        except ValueError: return await ctx.send("❌ Das war keine Zahl. Setup abgebrochen.")
        except asyncio.TimeoutError: return await ctx.send("❌ Zeit abgelaufen. Setup abgebrochen.")

        # 6. Whitelist (Rolle)
        await ctx.send("✅ Welche **Rolle** wird benötigt? (Erwähne Rolle, oder schreibe `keine`)")
        try:
            role_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            if role_msg.content.lower() == "cancel": return await ctx.send("❌ Abgebrochen.")
            required_role_id = None
            if role_msg.content.lower() != "keine":
                if not role_msg.role_mentions: return await ctx.send("❌ Keine Rolle erwähnt. Setup abgebrochen.")
                required_role_id = role_msg.role_mentions[0].id
        except asyncio.TimeoutError: return await ctx.send("❌ Zeit abgelaufen. Setup abgebrochen.")

        # 7. Bonus Rolle (2x Chance)
        await ctx.send("⭐ Gibt es eine **Bonus-Rolle** für doppelte Gewinnchance? (Erwähne Rolle, oder `keine`)")
        try:
            bonus_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            if bonus_msg.content.lower() == "cancel": return await ctx.send("❌ Abgebrochen.")
            bonus_role_id = None
            if bonus_msg.content.lower() != "keine":
                if not bonus_msg.role_mentions: return await ctx.send("❌ Keine Rolle erwähnt. Setup abgebrochen.")
                bonus_role_id = bonus_msg.role_mentions[0].id
        except asyncio.TimeoutError: return await ctx.send("❌ Zeit abgelaufen. Setup abgebrochen.")

        # 8. Blacklist
        await ctx.send("🚫 **Rollen/User ausschließen**? (Erwähne sie, oder `keine`)")
        try:
            bl_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            if bl_msg.content.lower() == "cancel": return await ctx.send("❌ Abgebrochen.")
            bl_roles = [r.id for r in bl_msg.role_mentions]
            bl_users = [u.id for u in bl_msg.mentions]
        except asyncio.TimeoutError: return await ctx.send("❌ Zeit abgelaufen. Setup abgebrochen.")

        # 9. Sponsor
        await ctx.send("🤝 Gibt es einen **Sponsor**? (Erwähne User/Rolle oder schreibe Text, oder `keiner`)")
        try:
            s_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            if s_msg.content.lower() == "cancel": return await ctx.send("❌ Abgebrochen.")
            sponsor = s_msg.content if s_msg.content.lower() != "keiner" else None
        except asyncio.TimeoutError: return await ctx.send("❌ Zeit abgelaufen. Setup abgebrochen.")

        # 10. Bild URL
        await ctx.send("🖼️ Soll ein **Bild** ans Embed angehängt werden? (Sende einen Bild-Link, oder `nein`)")
        try:
            img_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            if img_msg.content.lower() == "cancel": return await ctx.send("❌ Abgebrochen.")
            image_url = None
            if img_msg.content.lower() != "nein":
                image_url = img_msg.content if not img_msg.attachments else img_msg.attachments[0].url
        except asyncio.TimeoutError: return await ctx.send("❌ Zeit abgelaufen. Setup abgebrochen.")

        # Vorschau generieren
        end_time = datetime.now(timezone.utc) + delta
        gw_data = {
            "prize": prize, "winners_count": winners_count, "end_time": end_time.isoformat(),
            "host_id": host_id, "channel_id": target_channel.id, "message_id": 9999,
            "winners": [], "required_role_id": required_role_id,
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
            
            real_msg = await target_channel.send(embed=embed)
            await real_msg.add_reaction("🎉")
            
            gw_data["message_id"] = real_msg.id
            final_embed = await self.create_giveaway_embed(ctx.guild, gw_data)
            await real_msg.edit(embed=final_embed)
            
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
            if gw_data.get("ended"): return await ctx.send("❌ Bereits beendet.")
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
            for gw in active[:10]:
                end_time = datetime.fromisoformat(gw["end_time"])
                unix_ts = int(end_time.timestamp())
                embed.add_field(
                    name=f"🎁 {gw['prize']}",
                    value=f"**ID:** [{gw['message_id']}](https://discord.com/channels/{ctx.guild.id}/{gw['channel_id']}/{gw['message_id']})\n**Endet:** <t:{unix_ts}:R>",
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

            # Für Reroll nutzen wir die gespeicherten Teilnehmer aus der Datenbank
            participants = gw_data.get("participants", [])
            if not participants: return await ctx.send("❌ Keine Teilnehmer in der Datenbank gespeichert.")

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
                pool = [x for x in pool if x != w]

            winner_mentions = ", ".join(f"<@{wid}>" for wid in new_winners)
            try:
                old_msg = await target_channel.fetch_message(message_id)
                old_embed = old_msg.embeds[0] if old_msg.embeds else None
                if old_embed:
                    old_embed.color = discord.Color.orange()
                    old_embed.add_field(name="🔄 Rerolled", value=f"Neue Gewinner: {winner_mentions}", inline=False)
                    await old_msg.edit(embed=old_embed)
            except: pass
            await ctx.send(f"🔄 **Reroll!** Neue Gewinner: {winner_mentions}!")

    async def end_giveaway(self, guild, channel, message_id, gw_data):
        try:
            msg = await channel.fetch_message(message_id)
        except: return

        reaction = discord.utils.find(lambda r: str(r.emoji) == "🎉", msg.reactions)
        users = []
        if reaction:
            users = [user async for user in reaction.users() if not user.bot]

        valid_users = []
        for user in users:
            member = guild.get_member(user.id)
            if not member: continue
            
            if gw_data.get("required_role_id") and not any(r.id == gw_data["required_role_id"] for r in member.roles):
                continue
            if any(r.id in gw_data.get("blacklisted_roles", []) for r in member.roles):
                continue
            if user.id in gw_data.get("blacklisted_users", []):
                continue
                
            valid_users.append(member)

        num_winners = min(gw_data["winners_count"], len(valid_users))
        winners = []
        
        if num_winners > 0:
            bonus_role_id = gw_data.get("bonus_role_id")
            pool = []
            for member in valid_users:
                if bonus_role_id and any(r.id == bonus_role_id for r in member.roles):
                    pool.extend([member.id, member.id])
                else:
                    pool.append(member.id)

            for _ in range(num_winners):
                if not pool: break
                w = random.choice(pool)
                winners.append(w)
                pool = [x for x in pool if x != w]

        gw_data["winners"] = winners
        gw_data["participants"] = [u.id for u in valid_users] # WICHTIG: Speichern für spätere Rerolls!
        
        embed = await self.create_giveaway_embed(guild, gw_data, is_ended=True)
        
        try:
            await msg.edit(embed=embed)
            await msg.clear_reactions()
        except: pass
        
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

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Löscht sofort Reaktionen von Leuten, die nicht teilnehmen dürfen."""
        if payload.user_id == self.bot.user.id or str(payload.emoji) != "🎉":
            return
            
        guild = self.bot.get_guild(payload.guild_id)
        if not guild: return
        
        is_invalid = False
        
        async with self.config.guild(guild).giveaways() as giveaways:
            msg_id_str = str(payload.message_id)
            if msg_id_str not in giveaways: return
            
            gw = giveaways[msg_id_str]
            if gw.get("ended"): return
            
            member = guild.get_member(payload.user_id)
            if not member or member.bot: return
            
            if gw.get("required_role_id") and not any(r.id == gw["required_role_id"] for r in member.roles):
                is_invalid = True
            if any(r.id in gw.get("blacklisted_roles", []) for r in member.roles):
                is_invalid = True
            if payload.user_id in gw.get("blacklisted_users", []):
                is_invalid = True
                
        # Wenn der User nicht teilnehmen darf, sofort Reaktion löschen!
        if is_invalid:
            channel = guild.get_channel(payload.channel_id)
            if channel:
                try:
                    msg = await channel.fetch_message(payload.message_id)
                    await msg.remove_reaction("🎉", member)
                except: pass

async def setup(bot):
    await bot.add_cog(GiveawaySystem(bot))
