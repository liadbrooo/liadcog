import datetime
import statistics
from collections import Counter

import discord
from redbot.core import checks, commands


class MemberAnalyst(commands.Cog):
    """📊 Umfassende Mitgliederanalyse mit ASCII‑Diagrammen und detaillierten Statistiken."""

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
    def _create_bar_text(labels, values, max_bar_length=15):
        """
        Erstellt eine ASCII‑Balkengrafik als String.
        labels: Liste der Beschriftungen
        values: Liste der Zahlenwerte
        """
        if not labels or not values or len(labels) != len(values):
            return "Keine Daten vorhanden."

        max_value = max(values) if values else 1
        lines = []
        for label, val in zip(labels, values):
            # Skaliere die Balkenlänge
            bar_len = int((val / max_value) * max_bar_length) if max_value > 0 else 0
            bar = "█" * bar_len
            lines.append(f"{label:12} | {bar} {val}")
        return "\n".join(lines)

    @staticmethod
    def _create_histogram_text(data, bins=10, label="Wert"):
        """
        Erstellt ein Histogramm als ASCII‑Text.
        data: Liste von Zahlen
        bins: Anzahl der Bins
        """
        if not data:
            return "Keine Daten vorhanden."

        min_val = min(data)
        max_val = max(data)
        if min_val == max_val:
            # Alle Werte gleich
            lines = [f"{min_val} – {max_val}: {len(data)}"]
            return "\n".join(lines)

        bin_width = (max_val - min_val) / bins
        counts = [0] * bins
        for value in data:
            idx = int((value - min_val) // bin_width)
            if idx >= bins:
                idx = bins - 1
            counts[idx] += 1

        lines = []
        for i in range(bins):
            lower = min_val + i * bin_width
            upper = lower + bin_width
            if i == bins - 1:
                upper = max_val
            bar_len = int((counts[i] / max(counts)) * 15) if max(counts) > 0 else 0
            bar = "█" * bar_len
            lines.append(f"{lower:8.0f}-{upper:8.0f} | {bar} {counts[i]}")
        return "\n".join(lines)

    # ---------------------------------------------------------------
    # Befehl: [p]mitgliederstatistik - Gesamtübersicht
    # ---------------------------------------------------------------
    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def mitgliederstatistik(self, ctx):
        """Zeigt eine umfassende Statistikübersicht."""
        guild = ctx.guild
        members = guild.members
        humans = [m for m in members if not m.bot]
        bots = [m for m in members if m.bot]
        now = self._now()

        # Account-Alter (Menschen)
        account_ages = [(now - m.created_at).days for m in humans]
        avg_account_age = sum(account_ages) / len(account_ages) if account_ages else 0
        median_account_age = statistics.median(account_ages) if account_ages else 0

        # Server-Alter (Beitrittsdauer, Menschen)
        join_ages = [(now - m.joined_at).days for m in humans if m.joined_at]
        avg_join_age = sum(join_ages) / len(join_ages) if join_ages else 0
        median_join_age = statistics.median(join_ages) if join_ages else 0

        # Statuszählung (nur Menschen)
        status_counts = Counter(str(m.status) for m in humans)

        # Embed erstellen
        embed = discord.Embed(
            title=f"📊 Mitgliederstatistik für {guild.name}",
            color=discord.Color.blue(),
            timestamp=now,
        )
        embed.add_field(name="Gesamtmitglieder", value=str(len(members)), inline=True)
        embed.add_field(name="👤 Menschen", value=str(len(humans)), inline=True)
        embed.add_field(name="🤖 Bots", value=str(len(bots)), inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=False)

        # Status
        status_str = (
            f"🟢 Online: {status_counts.get('online', 0)}\n"
            f"🟡 Idle: {status_counts.get('idle', 0)}\n"
            f"🔴 DND: {status_counts.get('dnd', 0)}\n"
            f"⚫ Offline: {status_counts.get('offline', 0)}"
        )
        embed.add_field(name="Status (Menschen)", value=status_str, inline=True)

        # Altersangaben
        embed.add_field(
            name="Account-Alter (Menschen)",
            value=(
                f"Durchschnitt: {avg_account_age:.1f} Tage\n"
                f"Median: {median_account_age} Tage\n"
                f"≈ {self._human_years(int(avg_account_age))}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Server-Alter (Menschen)",
            value=(
                f"Durchschnitt: {avg_join_age:.1f} Tage\n"
                f"Median: {median_join_age} Tage\n"
                f"≈ {self._human_years(int(avg_join_age))}"
            ),
            inline=True,
        )

        # Älteste/neueste Konten
        if humans:
            oldest_acc = min(humans, key=lambda m: m.created_at)
            newest_acc = max(humans, key=lambda m: m.created_at)
            embed.add_field(
                name="👴 Ältester Account",
                value=f"{oldest_acc.mention} ({oldest_acc.created_at.strftime('%d.%m.%Y')})",
                inline=True,
            )
            embed.add_field(
                name="👶 Neuester Account",
                value=f"{newest_acc.mention} ({newest_acc.created_at.strftime('%d.%m.%Y')})",
                inline=True,
            )

        # Längstes/neuestes Mitglied
        if humans and all(m.joined_at for m in humans):
            oldest_join = min(humans, key=lambda m: m.joined_at)
            newest_join = max(humans, key=lambda m: m.joined_at)
            embed.add_field(
                name="🏆 Längstes Mitglied",
                value=f"{oldest_join.mention} ({oldest_join.joined_at.strftime('%d.%m.%Y')})",
                inline=True,
            )
            embed.add_field(
                name="🆕 Neuestes Mitglied",
                value=f"{newest_join.mention} ({newest_join.joined_at.strftime('%d.%m.%Y')})",
                inline=True,
            )

        await ctx.send(embed=embed)

    # ---------------------------------------------------------------
    # Befehl: [p]beitritte [Tage] - Beitritte der letzten X Tage
    # ---------------------------------------------------------------
    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def beitritte(self, ctx, days: int = 7):
        """Zeigt Anzahl und Diagramm der menschlichen Beitritte der letzten X Tage."""
        guild = ctx.guild
        now = self._now()
        threshold = now - datetime.timedelta(days=days)

        recent_joins = [
            m for m in guild.members
            if not m.bot and m.joined_at and m.joined_at >= threshold
        ]
        count = len(recent_joins)

        embed = discord.Embed(
            title=f"📥 Beitritte in den letzten {days} Tagen",
            description=f"**{count}** menschliche Mitglieder sind beigetreten.",
            color=discord.Color.green(),
        )

        # Neueste Mitglieder auflisten
        if recent_joins:
            sorted_joins = sorted(recent_joins, key=lambda m: m.joined_at, reverse=True)[:10]
            member_list = "\n".join(
                f"{m.mention} – {m.joined_at.strftime('%d.%m.%Y %H:%M')}"
                for m in sorted_joins
            )
            embed.add_field(name="Neueste Mitglieder", value=member_list, inline=False)

            # ASCII‑Diagramm: Beitritte pro Tag
            day_counts = Counter(m.joined_at.strftime("%d.%m.") for m in recent_joins)
            sorted_days = sorted(day_counts.items(), key=lambda x: datetime.datetime.strptime(x[0], "%d.%m."))
            labels = [d for d, _ in sorted_days]
            values = [v for _, v in sorted_days]

            diagram_text = self._create_bar_text(labels, values)
            embed.add_field(
                name="Beitritte pro Tag",
                value=f"```\n{diagram_text}\n```",
                inline=False,
            )

        await ctx.send(embed=embed)

    # ---------------------------------------------------------------
    # Befehl: [p]accountalter [Mitglied] - Detailliertes Alter eines Users
    # ---------------------------------------------------------------
    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def accountalter(self, ctx, member: discord.Member = None):
        """Zeigt das Account- und Server-Alter eines Mitglieds."""
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
            value=member.created_at.strftime("%d.%m.%Y %H:%M UTC"),
            inline=False,
        )
        embed.add_field(
            name="Account-Alter",
            value=f"{account_age_days} Tage\n≈ {self._human_years(account_age_days)}",
            inline=True,
        )

        if join_age_days is not None:
            embed.add_field(
                name="Server beigetreten am",
                value=member.joined_at.strftime("%d.%m.%Y %H:%M UTC"),
                inline=False,
            )
            embed.add_field(
                name="Server-Alter",
                value=f"{join_age_days} Tage\n≈ {self._human_years(join_age_days)}",
                inline=True,
            )

        await ctx.send(embed=embed)

    # ---------------------------------------------------------------
    # Befehl: [p]aeltesteaccounts [Anzahl] - Älteste Accounts
    # ---------------------------------------------------------------
    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def aeltesteaccounts(self, ctx, amount: int = 5):
        """Listet die ältesten Accounts (Menschen) auf dem Server."""
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
                f"{i}. {m.mention} – {age_days} Tage ({m.created_at.strftime('%d.%m.%Y')})"
            )
        embed.description = "\n".join(lines)

        await ctx.send(embed=embed)

    # ---------------------------------------------------------------
    # Befehl: [p]alterhistogramm [Bins] - Histogramm der Account-Alter
    # ---------------------------------------------------------------
    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def alterhistogramm(self, ctx, bins: int = 10):
        """Erstellt ein Histogramm (ASCII) der Account-Alter aller Menschen."""
        guild = ctx.guild
        humans = [m for m in guild.members if not m.bot]
        if not humans:
            await ctx.send("Keine menschlichen Mitglieder gefunden.")
            return

        now = self._now()
        ages = [(now - m.created_at).days for m in humans]

        hist_text = self._create_histogram_text(ages, bins=bins, label="Tage")
        embed = discord.Embed(
            title="📊 Histogramm der Account-Alter",
            description=f"```\n{hist_text}\n```",
            color=discord.Color.blue(),
        )
        await ctx.send(embed=embed)

    # ---------------------------------------------------------------
    # Befehl: [p]beitrittshistogramm [Bins] - Histogramm der Server-Alter
    # ---------------------------------------------------------------
    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def beitrittshistogramm(self, ctx, bins: int = 10):
        """Erstellt ein Histogramm (ASCII) der Server-Alter aller Menschen."""
        guild = ctx.guild
        humans = [m for m in guild.members if not m.bot and m.joined_at]
        if not humans:
            await ctx.send("Keine menschlichen Mitglieder mit Beitrittsdatum gefunden.")
            return

        now = self._now()
        ages = [(now - m.joined_at).days for m in humans]

        hist_text = self._create_histogram_text(ages, bins=bins, label="Tage")
        embed = discord.Embed(
            title="📊 Histogramm der Server-Alter",
            description=f"```\n{hist_text}\n```",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    # ---------------------------------------------------------------
    # Befehl: [p]statusdiagramm - Statusverteilung als Text
    # ---------------------------------------------------------------
    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def statusdiagramm(self, ctx):
        """Zeigt die Statusverteilung (Menschen) als Textdiagramm."""
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
                bar_len = int((count / total) * 15) if total else 0
                bar = "█" * bar_len
                lines.append(f"{s.capitalize():8} | {bar} {count} ({percent:.1f}%)")

        text = "\n".join(lines)
        embed = discord.Embed(
            title="📊 Statusverteilung (Menschen)",
            description=f"```\n{text}\n```",
            color=discord.Color.purple(),
        )
        await ctx.send(embed=embed)

    # ---------------------------------------------------------------
    # Befehl: [p]beitrittswachstum [Tage] - Wachstumsdiagramm (umbenannt)
    # ---------------------------------------------------------------
    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def beitrittswachstum(self, ctx, days: int = 30):
        """Zeigt ein ASCII‑Balkendiagramm der Beitritte pro Tag über die letzten X Tage."""
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

        # Zähle pro Tag
        day_counts = Counter(m.joined_at.strftime("%d.%m.") for m in recent_joins)
        sorted_days = sorted(day_counts.items(), key=lambda x: datetime.datetime.strptime(x[0], "%d.%m."))
        labels = [d for d, _ in sorted_days]
        values = [v for _, v in sorted_days]

        diagram_text = self._create_bar_text(labels, values)
        embed = discord.Embed(
            title=f"📈 Beitrittswachstum – Beitritte pro Tag (letzte {days} Tage)",
            description=f"```\n{diagram_text}\n```",
            color=discord.Color.orange(),
        )
        await ctx.send(embed=embed)


# ---------------------------------------------------------------
# Setup-Funktion für RedBot (async für neue Versionen)
# ---------------------------------------------------------------
async def setup(bot):
    await bot.add_cog(MemberAnalyst(bot))
