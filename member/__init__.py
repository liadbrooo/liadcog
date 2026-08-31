import datetime
import statistics
from collections import Counter

import discord
from redbot.core import checks, commands


class MemberAnalyst(commands.Cog):
    """📊 Mitgliederanalyse – klar, übersichtlich, aussagekräftig."""

    def __init__(self, bot):
        self.bot = bot

    # ---------------------------------------------------------------
    # Hilfsfunktionen
    # ---------------------------------------------------------------
    @staticmethod
    def _now():
        return datetime.datetime.now(datetime.timezone.utc)

    @staticmethod
    def _human_years(days: int) -> str:
        """Wandelt Tage in lesbare Jahre/Monate/Tage um."""
        years, rem = divmod(days, 365)
        months, days = divmod(rem, 30)
        parts = []
        if years:
            parts.append(f"{years} Jahre")
        if months:
            parts.append(f"{months} Monate")
        if days or not parts:
            parts.append(f"{days} Tage")
        return ", ".join(parts)

    @staticmethod
    def _format_bar_chart(labels, values, max_bar_length=15):
        """Erzeugt eine kompakte, gut lesbare ASCII‑Balkengrafik."""
        if not labels or not values or len(labels) != len(values):
            return "Keine Daten vorhanden."

        max_value = max(values) if values else 1
        lines = []
        for label, val in zip(labels, values):
            bar_len = int((val / max_value) * max_bar_length) if max_value > 0 else 0
            bar = "■" * bar_len  # Verwendung von ■ für bessere Lesbarkeit
            lines.append(f"`{label:10}` {bar} {val}")
        return "\n".join(lines)

    @staticmethod
    def _format_histogram_text(data, bins=10, unit="Tage"):
        """Erzeugt ein übersichtliches Histogramm als Text."""
        if not data:
            return "Keine Daten vorhanden."

        min_val = min(data)
        max_val = max(data)
        if min_val == max_val:
            return f"Alle Werte: {min_val} {unit}"

        bin_width = (max_val - min_val) / bins
        counts = [0] * bins
        for value in data:
            idx = int((value - min_val) // bin_width)
            if idx >= bins:
                idx = bins - 1
            counts[idx] += 1

        max_count = max(counts) if counts else 1
        lines = []
        for i in range(bins):
            lower = min_val + i * bin_width
            upper = lower + bin_width
            if i == bins - 1:
                upper = max_val
            bar_len = int((counts[i] / max_count) * 15) if max_count else 0
            bar = "■" * bar_len
            lines.append(f"`{lower:6.0f}-{upper:6.0f}` {bar} {counts[i]}")
        return "\n".join(lines)

    # ---------------------------------------------------------------
    # Befehl: [p]mitgliederstatistik - Gesamtübersicht
    # ---------------------------------------------------------------
    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def mitgliederstatistik(self, ctx):
        """Übersichtliche Gesamtstatistik des Servers."""
        guild = ctx.guild
        members = guild.members
        humans = [m for m in members if not m.bot]
        bots = [m for m in members if m.bot]
        now = self._now()

        # Berechnungen
        account_ages = [(now - m.created_at).days for m in humans]
        avg_account_age = sum(account_ages) / len(account_ages) if account_ages else 0
        median_account_age = statistics.median(account_ages) if account_ages else 0
        join_ages = [(now - m.joined_at).days for m in humans if m.joined_at]
        avg_join_age = sum(join_ages) / len(join_ages) if join_ages else 0
        median_join_age = statistics.median(join_ages) if join_ages else 0
        status_counts = Counter(str(m.status) for m in humans)

        # Embed 1: Grunddaten & Status
        embed = discord.Embed(
            title=f"📊 Mitgliederstatistik für {guild.name}",
            color=discord.Color.blue(),
            timestamp=now,
        )
        embed.add_field(name="👥 Gesamt", value=f"**{len(members)}**", inline=True)
        embed.add_field(name="👤 Menschen", value=f"**{len(humans)}**", inline=True)
        embed.add_field(name="🤖 Bots", value=f"**{len(bots)}**", inline=True)

        status_lines = [
            f"🟢 Online: **{status_counts.get('online', 0)}**",
            f"🟡 Idle: **{status_counts.get('idle', 0)}**",
            f"🔴 DND: **{status_counts.get('dnd', 0)}**",
            f"⚫ Offline: **{status_counts.get('offline', 0)}**",
        ]
        embed.add_field(
            name="📡 Status (nur Menschen)",
            value="\n".join(status_lines),
            inline=False,
        )
        await ctx.send(embed=embed)

        # Embed 2: Altersstatistiken
        embed2 = discord.Embed(
            title="📅 Altersstatistiken (Menschen)",
            color=discord.Color.teal(),
        )
        embed2.add_field(
            name="Account-Alter",
            value=(
                f"Ø: **{avg_account_age:.0f} Tage**\n"
                f"Median: **{median_account_age:.0f} Tage**\n"
                f"≈ {self._human_years(int(avg_account_age))}"
            ),
            inline=True,
        )
        embed2.add_field(
            name="Server-Alter",
            value=(
                f"Ø: **{avg_join_age:.0f} Tage**\n"
                f"Median: **{median_join_age:.0f} Tage**\n"
                f"≈ {self._human_years(int(avg_join_age))}"
            ),
            inline=True,
        )

        # Extremwerte
        if humans:
            oldest_acc = min(humans, key=lambda m: m.created_at)
            newest_acc = max(humans, key=lambda m: m.created_at)
            embed2.add_field(
                name="👴 Ältester Account",
                value=f"{oldest_acc.mention}\n({oldest_acc.created_at.strftime('%d.%m.%Y')})",
                inline=True,
            )
            embed2.add_field(
                name="👶 Neuester Account",
                value=f"{newest_acc.mention}\n({newest_acc.created_at.strftime('%d.%m.%Y')})",
                inline=True,
            )

        if humans and all(m.joined_at for m in humans):
            oldest_join = min(humans, key=lambda m: m.joined_at)
            newest_join = max(humans, key=lambda m: m.joined_at)
            embed2.add_field(
                name="🏆 Längstes Mitglied",
                value=f"{oldest_join.mention}\n({oldest_join.joined_at.strftime('%d.%m.%Y')})",
                inline=True,
            )
            embed2.add_field(
                name="🆕 Neuestes Mitglied",
                value=f"{newest_join.mention}\n({newest_join.joined_at.strftime('%d.%m.%Y')})",
                inline=True,
            )

        await ctx.send(embed=embed2)

    # ---------------------------------------------------------------
    # Befehl: [p]beitritte [Tage]
    # ---------------------------------------------------------------
    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def beitritte(self, ctx, days: int = 7):
        """Beitritte der letzten X Tage mit Tagesübersicht."""
        guild = ctx.guild
        now = self._now()
        threshold = now - datetime.timedelta(days=days)
        recent_joins = [
            m for m in guild.members
            if not m.bot and m.joined_at and m.joined_at >= threshold
        ]
        count = len(recent_joins)

        embed = discord.Embed(
            title=f"📥 Beitritte (letzte {days} Tage)",
            description=f"Insgesamt **{count}** neue menschliche Mitglieder.",
            color=discord.Color.green(),
        )

        if recent_joins:
            # Neueste 5 Mitglieder
            sorted_joins = sorted(recent_joins, key=lambda m: m.joined_at, reverse=True)[:5]
            member_list = "\n".join(
                f"{m.mention} – {m.joined_at.strftime('%d.%m.%Y')}"
                for m in sorted_joins
            )
            embed.add_field(name="Neueste Mitglieder", value=member_list, inline=False)

            # Tagesdiagramm
            day_counts = Counter(m.joined_at.strftime("%d.%m.") for m in recent_joins)
            sorted_days = sorted(day_counts.items(), key=lambda x: datetime.datetime.strptime(x[0], "%d.%m."))
            labels = [d for d, _ in sorted_days]
            values = [v for _, v in sorted_days]
            diagram = self._format_bar_chart(labels, values)
            embed.add_field(
                name="Beitritte pro Tag",
                value=f"```\n{diagram}\n```",
                inline=False,
            )

        await ctx.send(embed=embed)

    # ---------------------------------------------------------------
    # Befehl: [p]accountalter [Mitglied]
    # ---------------------------------------------------------------
    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def accountalter(self, ctx, member: discord.Member = None):
        """Detailliertes Alter eines Accounts und der Serverzugehörigkeit."""
        member = member or ctx.author
        now = self._now()
        account_age_days = (now - member.created_at).days
        join_age_days = (now - member.joined_at).days if member.joined_at else None

        embed = discord.Embed(
            title=f"🕒 Account-Alter von {member.display_name}",
            color=member.color,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name="Account erstellt am",
            value=f"`{member.created_at.strftime('%d.%m.%Y %H:%M')} UTC`",
            inline=False,
        )
        embed.add_field(
            name="Account-Alter",
            value=f"**{account_age_days} Tage**\n≈ {self._human_years(account_age_days)}",
            inline=True,
        )

        if join_age_days is not None:
            embed.add_field(
                name="Server beigetreten am",
                value=f"`{member.joined_at.strftime('%d.%m.%Y %H:%M')} UTC`",
                inline=False,
            )
            embed.add_field(
                name="Server-Alter",
                value=f"**{join_age_days} Tage**\n≈ {self._human_years(join_age_days)}",
                inline=True,
            )

        await ctx.send(embed=embed)

    # ---------------------------------------------------------------
    # Befehl: [p]aeltesteaccounts [Anzahl]
    # ---------------------------------------------------------------
    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def aeltesteaccounts(self, ctx, amount: int = 5):
        """Listet die ältesten Accounts (Menschen) auf."""
        guild = ctx.guild
        humans = [m for m in guild.members if not m.bot]
        if not humans:
            await ctx.send("Keine menschlichen Mitglieder gefunden.")
            return

        oldest = sorted(humans, key=lambda m: m.created_at)[:amount]
        now = self._now()

        embed = discord.Embed(
            title=f"👴 Älteste Accounts (Top {amount})",
            color=discord.Color.gold(),
        )
        lines = []
        for i, m in enumerate(oldest, 1):
            age_days = (now - m.created_at).days
            lines.append(
                f"**{i}.** {m.mention} – {age_days} Tage\n"
                f"    └ Erstellt: {m.created_at.strftime('%d.%m.%Y')}"
            )
        embed.description = "\n".join(lines)

        await ctx.send(embed=embed)

    # ---------------------------------------------------------------
    # Befehl: [p]alterhistogramm [Bins]
    # ---------------------------------------------------------------
    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def alterhistogramm(self, ctx, bins: int = 10):
        """Histogramm der Account-Alter (Menschen)."""
        guild = ctx.guild
        humans = [m for m in guild.members if not m.bot]
        if not humans:
            await ctx.send("Keine menschlichen Mitglieder gefunden.")
            return

        now = self._now()
        ages = [(now - m.created_at).days for m in humans]
        hist_text = self._format_histogram_text(ages, bins=bins, unit="Tage")

        embed = discord.Embed(
            title="📊 Verteilung der Account-Alter",
            description=f"```\n{hist_text}\n```",
            color=discord.Color.blue(),
        )
        await ctx.send(embed=embed)

    # ---------------------------------------------------------------
    # Befehl: [p]beitrittshistogramm [Bins]
    # ---------------------------------------------------------------
    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def beitrittshistogramm(self, ctx, bins: int = 10):
        """Histogramm der Server-Alter (Beitrittsdauer)."""
        guild = ctx.guild
        humans = [m for m in guild.members if not m.bot and m.joined_at]
        if not humans:
            await ctx.send("Keine menschlichen Mitglieder mit Beitrittsdatum gefunden.")
            return

        now = self._now()
        ages = [(now - m.joined_at).days for m in humans]
        hist_text = self._format_histogram_text(ages, bins=bins, unit="Tage")

        embed = discord.Embed(
            title="📊 Verteilung der Server-Alter",
            description=f"```\n{hist_text}\n```",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    # ---------------------------------------------------------------
    # Befehl: [p]statusdiagramm
    # ---------------------------------------------------------------
    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def statusdiagramm(self, ctx):
        """Statusverteilung der Menschen als übersichtliches Diagramm."""
        guild = ctx.guild
        humans = [m for m in guild.members if not m.bot]
        if not humans:
            await ctx.send("Keine menschlichen Mitglieder gefunden.")
            return

        status_counts = Counter(str(m.status) for m in humans)
        order = ["online", "idle", "dnd", "offline"]
        lines = []
        total = len(humans)
        for s in order:
            if s in status_counts:
                count = status_counts[s]
                percent = (count / total) * 100 if total else 0
                bar_len = int((count / total) * 12) if total else 0
                bar = "■" * bar_len
                lines.append(f"{s.capitalize():8} {bar} {count} ({percent:.1f}%)")

        embed = discord.Embed(
            title="📡 Statusverteilung (Menschen)",
            description=f"```\n" + "\n".join(lines) + "\n```",
            color=discord.Color.purple(),
        )
        await ctx.send(embed=embed)

    # ---------------------------------------------------------------
    # Befehl: [p]beitrittswachstum [Tage]
    # ---------------------------------------------------------------
    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def beitrittswachstum(self, ctx, days: int = 30):
        """Beitrittswachstum pro Tag über die letzten X Tage."""
        guild = ctx.guild
        now = self._now()
        threshold = now - datetime.timedelta(days=days)
        recent_joins = [
            m for m in guild.members
            if not m.bot and m.joined_at and m.joined_at >= threshold
        ]

        if not recent_joins:
            await ctx.send("Keine Beitritte in diesem Zeitraum.")
            return

        day_counts = Counter(m.joined_at.strftime("%d.%m.") for m in recent_joins)
        sorted_days = sorted(day_counts.items(), key=lambda x: datetime.datetime.strptime(x[0], "%d.%m."))
        labels = [d for d, _ in sorted_days]
        values = [v for _, v in sorted_days]
        diagram = self._format_bar_chart(labels, values)

        embed = discord.Embed(
            title=f"📈 Beitrittswachstum (letzte {days} Tage)",
            description=f"```\n{diagram}\n```",
            color=discord.Color.orange(),
        )
        await ctx.send(embed=embed)


# ---------------------------------------------------------------
# Setup-Funktion
# ---------------------------------------------------------------
async def setup(bot):
    await bot.add_cog(MemberAnalyst(bot))
