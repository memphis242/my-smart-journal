package dev.msj.journal.ui.theme

import androidx.compose.foundation.text.selection.TextSelectionColors
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.LineHeightStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import dev.msj.journal.R
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.LocalTextSelectionColors

private fun plexSans(weight: Int): Font =
    Font(
        R.font.ibm_plex_sans_variable,
        FontWeight(weight),
        variationSettings = FontVariation.Settings(FontVariation.weight(weight), FontVariation.width(100f)),
    )

private fun sourceSerif(weight: Int): Font =
    Font(
        R.font.source_serif_4_variable,
        FontWeight(weight),
        variationSettings = FontVariation.Settings(FontVariation.weight(weight)),
    )

/** IBM Plex Sans: interface text. */
val PlexSans = FontFamily(plexSans(400), plexSans(500), plexSans(600))

/** IBM Plex Mono: dates, times, counts, codes and identifiers. */
val PlexMono =
    FontFamily(
        Font(R.font.ibm_plex_mono_regular, FontWeight.Normal),
        Font(R.font.ibm_plex_mono_medium, FontWeight.Medium),
    )

/** Source Serif 4: the journal's own words (titles, summaries, entry text). */
val SourceSerif = FontFamily(sourceSerif(400), sourceSerif(600))

private val Trim =
    LineHeightStyle(alignment = LineHeightStyle.Alignment.Center, trim = LineHeightStyle.Trim.None)

/** Named text roles; components pick a role, never a raw size. */
@Immutable
data class JournalType(
    /** Interface base: buttons, labels, list text. */
    val ui: TextStyle,
    val uiStrong: TextStyle,
    val uiSmall: TextStyle,
    /** Small section label above a block ("Summary", "Text"). */
    val label: TextStyle,
    val barTitle: TextStyle,
    /** Tabular figures: dates, counts, codes. */
    val mono: TextStyle,
    val monoSmall: TextStyle,
    val monoLarge: TextStyle,
    /** Entry text and summaries. */
    val prose: TextStyle,
    val proseTitle: TextStyle,
    /** One-line entry preview in lists. */
    val rowText: TextStyle,
)

private val BaseTypes =
    JournalType(
        ui = TextStyle(fontFamily = PlexSans, fontWeight = FontWeight(400), fontSize = 15.sp, lineHeight = 21.sp),
        uiStrong = TextStyle(fontFamily = PlexSans, fontWeight = FontWeight(600), fontSize = 15.sp, lineHeight = 21.sp),
        uiSmall = TextStyle(fontFamily = PlexSans, fontWeight = FontWeight(400), fontSize = 13.sp, lineHeight = 18.sp),
        label =
            TextStyle(
                fontFamily = PlexSans,
                fontWeight = FontWeight(500),
                fontSize = 12.sp,
                lineHeight = 16.sp,
                letterSpacing = 0.06.em,
            ),
        barTitle = TextStyle(fontFamily = PlexSans, fontWeight = FontWeight(600), fontSize = 18.sp, lineHeight = 24.sp),
        mono =
            TextStyle(
                fontFamily = PlexMono,
                fontWeight = FontWeight.Normal,
                fontSize = 13.sp,
                lineHeight = 18.sp,
                fontFeatureSettings = "tnum",
            ),
        monoSmall =
            TextStyle(
                fontFamily = PlexMono,
                fontWeight = FontWeight.Normal,
                fontSize = 12.sp,
                lineHeight = 16.sp,
                fontFeatureSettings = "tnum",
            ),
        monoLarge =
            TextStyle(
                fontFamily = PlexMono,
                fontWeight = FontWeight.Medium,
                fontSize = 40.sp,
                lineHeight = 48.sp,
                fontFeatureSettings = "tnum",
            ),
        prose =
            TextStyle(
                fontFamily = SourceSerif,
                fontWeight = FontWeight(400),
                fontSize = 17.sp,
                lineHeight = 27.sp,
                lineHeightStyle = Trim,
            ),
        proseTitle = TextStyle(fontFamily = SourceSerif, fontWeight = FontWeight(600), fontSize = 22.sp, lineHeight = 28.sp),
        rowText = TextStyle(fontFamily = SourceSerif, fontWeight = FontWeight(400), fontSize = 16.sp, lineHeight = 22.sp),
    )

val LocalJournalType = staticCompositionLocalOf { BaseTypes }

/** Corners are set deliberately small: a drawing sheet, not a pill-shaped app. */
object JournalShapes {
    val small = RoundedCornerShape(2.dp)
    val control = RoundedCornerShape(4.dp)
    val sheet = RoundedCornerShape(6.dp)
}

/** Spacing and size scale (dp), mirroring the web's 4-px grid. */
object Space {
    val s1 = 4.dp
    val s2 = 8.dp
    val s3 = 12.dp
    val s4 = 16.dp
    val s5 = 20.dp
    val s6 = 24.dp
    val s7 = 32.dp
    val gutter = 16.dp
    val hairline = 1.dp
    val rowHeight = 56.dp
    val control = 48.dp
    val dateColumn = 64.dp
}

object Journal {
    val colors: JournalColors
        @Composable @ReadOnlyComposable
        get() = LocalJournalColors.current

    val type: JournalType
        @Composable @ReadOnlyComposable
        get() = LocalJournalType.current
}

private fun schemeFor(c: JournalColors): ColorScheme {
    val base = if (c.isDark) darkColorScheme() else lightColorScheme()
    return base.copy(
        primary = c.accent,
        onPrimary = c.accentInk,
        primaryContainer = c.accentTint,
        onPrimaryContainer = c.ink,
        secondary = c.ink2,
        onSecondary = c.bg,
        secondaryContainer = c.accentTint,
        onSecondaryContainer = c.ink,
        tertiary = c.ink2,
        onTertiary = c.bg,
        background = c.bg,
        onBackground = c.ink,
        surface = c.bg,
        onSurface = c.ink,
        surfaceVariant = c.bgBand,
        onSurfaceVariant = c.ink2,
        surfaceContainerLowest = c.surface,
        surfaceContainerLow = c.surface,
        surfaceContainer = c.surface,
        surfaceContainerHigh = c.surfaceRaised,
        surfaceContainerHighest = c.surfaceRaised,
        surfaceBright = c.surfaceRaised,
        surfaceDim = c.bgBand,
        inverseSurface = c.ink,
        inverseOnSurface = c.bg,
        inversePrimary = c.accentTintStrong,
        outline = c.fieldBorder,
        outlineVariant = c.rule,
        error = c.stFailed,
        onError = c.bg,
        errorContainer = c.stFailedTint,
        onErrorContainer = c.ink,
        scrim = c.backdrop,
        surfaceTint = c.bg,
    )
}

private fun materialTypography(t: JournalType): Typography {
    val base = Typography()
    return base.copy(
        bodyLarge = t.ui,
        bodyMedium = t.ui,
        bodySmall = t.uiSmall,
        labelLarge = t.uiStrong.copy(fontSize = 14.sp),
        labelMedium = t.label,
        labelSmall = t.uiSmall,
        titleLarge = t.barTitle,
        titleMedium = t.uiStrong,
        titleSmall = t.uiStrong.copy(fontSize = 14.sp),
        headlineSmall = t.proseTitle,
    )
}

@Composable
fun JournalTheme(
    dark: Boolean,
    content: @Composable () -> Unit,
) {
    val colors = if (dark) DarkColors else LightColors
    val selection = TextSelectionColors(handleColor = colors.accent, backgroundColor = colors.accentTintStrong)
    CompositionLocalProvider(
        LocalJournalColors provides colors,
        LocalJournalType provides BaseTypes,
        LocalTextSelectionColors provides selection,
    ) {
        MaterialTheme(
            colorScheme = schemeFor(colors),
            typography = materialTypography(BaseTypes),
            shapes =
                Shapes(
                    extraSmall = JournalShapes.small,
                    small = JournalShapes.control,
                    medium = JournalShapes.control,
                    large = JournalShapes.sheet,
                    extraLarge = JournalShapes.sheet,
                ),
            content = content,
        )
    }
}
