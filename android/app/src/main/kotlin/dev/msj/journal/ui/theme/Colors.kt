package dev.msj.journal.ui.theme

import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

/**
 * Design tokens, ported from web/src/styles/tokens.css. Components use only
 * these names; nothing carries a literal color that works in one theme only.
 *
 * Light: ruled notebook paper with fountain-pen blue-black ink as the accent.
 * Dark: a graphite desk at night, paper-white text, a cooler and brighter ink.
 * The state colors (queued, progress, done, attention, failed) carry state only.
 */
@Immutable
data class JournalColors(
    val isDark: Boolean,
    val bg: Color,
    val bgBand: Color,
    val surface: Color,
    val surfaceRaised: Color,
    val field: Color,
    val fieldReadonly: Color,
    val fieldBorder: Color,
    val fieldBorderStrong: Color,
    val rule: Color,
    val ruleStrong: Color,
    /** The notebook margin line beside the date column; a neutral rule, not a state. */
    val margin: Color,
    val ink: Color,
    val ink2: Color,
    val ink3: Color,
    val inkDisabled: Color,
    val accent: Color,
    val accentPressed: Color,
    val accentInk: Color,
    val accentTint: Color,
    val accentTintStrong: Color,
    val hover: Color,
    val press: Color,
    val backdrop: Color,
    val stQueued: Color,
    val stProgress: Color,
    val stDone: Color,
    val stAttention: Color,
    val stFailed: Color,
    val stFailedTint: Color,
    val stAttentionTint: Color,
    val stProgressTint: Color,
)

val LightColors =
    JournalColors(
        isDark = false,
        bg = Color(0xFFF4F2EC),
        bgBand = Color(0xFFECE9E1),
        surface = Color(0xFFFBFAF7),
        surfaceRaised = Color(0xFFFFFFFF),
        field = Color(0xFFFFFFFF),
        fieldReadonly = Color(0xFFF0EDE6),
        fieldBorder = Color(0xFF8A8375),
        fieldBorderStrong = Color(0xFF5C564C),
        rule = Color(0xFFDCD7CC),
        ruleStrong = Color(0xFFC3BCAE),
        margin = Color(0xFFCFC8BA),
        ink = Color(0xFF1C1B18),
        ink2 = Color(0xFF4C4841),
        ink3 = Color(0xFF686358),
        inkDisabled = Color(0xFF9F998D),
        accent = Color(0xFF2350A0),
        accentPressed = Color(0xFF1B4189),
        accentInk = Color(0xFFFFFFFF),
        accentTint = Color(0xFFE2E8F4),
        accentTintStrong = Color(0xFFCDD8EE),
        hover = Color(0x0D1C1B18),
        press = Color(0x171C1B18),
        backdrop = Color(0x6B1E1C18),
        stQueued = Color(0xFF6C665B),
        stProgress = Color(0xFF0B6A77),
        stDone = Color(0xFF2B6C3E),
        stAttention = Color(0xFF965600),
        stFailed = Color(0xFFAD281D),
        stFailedTint = Color(0xFFF6E3DF),
        stAttentionTint = Color(0xFFF4E9D6),
        stProgressTint = Color(0xFFDCECEE),
    )

val DarkColors =
    JournalColors(
        isDark = true,
        bg = Color(0xFF151618),
        bgBand = Color(0xFF1B1D20),
        surface = Color(0xFF1F2124),
        surfaceRaised = Color(0xFF26292D),
        field = Color(0xFF111214),
        fieldReadonly = Color(0xFF1A1C1F),
        fieldBorder = Color(0xFF6B6F77),
        fieldBorderStrong = Color(0xFF9A9EA6),
        rule = Color(0xFF2A2D31),
        ruleStrong = Color(0xFF3B3F45),
        margin = Color(0xFF34373C),
        ink = Color(0xFFEBE7DF),
        ink2 = Color(0xFFBCB8AF),
        ink3 = Color(0xFF959189),
        inkDisabled = Color(0xFF64625E),
        accent = Color(0xFF8AACF0),
        accentPressed = Color(0xFFA8C2F6),
        accentInk = Color(0xFF0C1427),
        accentTint = Color(0xFF1D283B),
        accentTintStrong = Color(0xFF26365A),
        hover = Color(0x0FEBE7DF),
        press = Color(0x1AEBE7DF),
        backdrop = Color(0x9E040506),
        stQueued = Color(0xFF9A978F),
        stProgress = Color(0xFF52B6C2),
        stDone = Color(0xFF72C088),
        stAttention = Color(0xFFE1A34C),
        stFailed = Color(0xFFF07C70),
        stFailedTint = Color(0xFF3A2321),
        stAttentionTint = Color(0xFF382C1B),
        stProgressTint = Color(0xFF183238),
    )

val LocalJournalColors = staticCompositionLocalOf { LightColors }
