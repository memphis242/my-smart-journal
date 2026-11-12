package dev.msj.journal.ui.components

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.unit.dp

/**
 * One line-glyph set, shared with the web client (web/src/components/Icon.tsx):
 * 20 x 20 units, 1.5-unit round strokes, hollow. Tinted by the caller.
 */
object JournalIcons {
    val Audio: ImageVector = icon("audio", "M4 9v2M7 6.5v7M10 4v12M13 6.5v7M16 9v2")
    val Back: ImageVector = icon("back", "M16 10H4.5M9.25 5.25 4.5 10l4.75 4.75")
    val Check: ImageVector = icon("check", "M4.5 10.5 8 14l7.5-8")
    val ChevronDown: ImageVector = icon("chevron-down", "m5.5 8 4.5 4.5L14.5 8")
    val ChevronLeft: ImageVector = icon("chevron-left", "M12.5 4.5 7 10l5.5 5.5")
    val ChevronRight: ImageVector = icon("chevron-right", "M7.5 4.5 13 10l-5.5 5.5")
    val Close: ImageVector = icon("close", "m5.5 5.5 9 9m0-9-9 9")
    val Copy: ImageVector =
        icon(
            "copy",
            rect(7f, 7f, 9f, 9.5f, 1.25f),
            "M13 7V5.25C13 4.56 12.44 4 11.75 4h-6.5C4.56 4 4 4.56 4 5.25v6.5c0 .69.56 1.25 1.25 1.25H7",
        )
    val Edit: ImageVector = icon("edit", "M12.75 4.25 15.75 7.25 7.5 15.5 4 16l.5-3.5z", "m11 6 3 3")
    val Filter: ImageVector = icon("filter", "M3.5 4.5h13l-5 6.25v4.5l-3 1.5v-6z")
    val Mic: ImageVector = icon("mic", rect(7.5f, 3f, 5f, 9f, 2.5f), "M5 9.75a5 5 0 0 0 10 0M10 14.75v2.5")
    val Moon: ImageVector = icon("moon", "M15.75 12.25A6.25 6.25 0 0 1 7.75 4.25a6.25 6.25 0 1 0 8 8z")
    val More: ImageVector = icon("more", circle(5f, 10f, 0.6f), circle(10f, 10f, 0.6f), circle(15f, 10f, 0.6f))
    val Plus: ImageVector = icon("plus", "M10 4.5v11M4.5 10h11")
    val Retry: ImageVector = icon("retry", "M15.25 10a5.25 5.25 0 1 1-1.54-3.71", "M15.25 3.75v3.5h-3.5")
    val Search: ImageVector = icon("search", circle(8.75f, 8.75f, 4.75f), "m12.25 12.25 4.25 4.25")
    val SignOut: ImageVector =
        icon(
            "sign-out",
            "M8 4.5H5.5C4.67 4.5 4 5.17 4 6v8c0 .83.67 1.5 1.5 1.5H8",
            "M11.75 13.5 15.25 10l-3.5-3.5M15.25 10H8",
        )
    val Stop: ImageVector = icon("stop", rect(5.5f, 5.5f, 9f, 9f, 1f))
    val Sun: ImageVector =
        icon(
            "sun",
            circle(10f, 10f, 3.25f),
            "M10 2.75v1.5M10 15.75v1.5M2.75 10h1.5M15.75 10h1.5M4.87 4.87l1.06 1.06M14.07 14.07l1.06 1.06" +
                "M4.87 15.13l1.06-1.06M14.07 5.93l1.06-1.06",
        )
    val Text: ImageVector = icon("text", "M4.5 5.5h11M4.5 9h11M4.5 12.5h11M4.5 16h6")
    val Trash: ImageVector = icon("trash", "M4.5 6h11M8 6V4.25h4V6", "m6 6 .75 10h6.5L14 6")
    val Upload: ImageVector = icon("upload", "M10 13V4M6.25 7.75 10 4l3.75 3.75M4 16.25h12")
    val Outbox: ImageVector = icon("outbox", "M3.5 11.5V15.5h13v-4", "M3.5 11.5h3.75l1 1.75h3.5l1-1.75h3.75", "M10 9.5V3.5M7.5 6 10 3.5 12.5 6")
    val Settings: ImageVector =
        icon("settings", "M4 6h12M4 14h12", circle(7.5f, 6f, 1.75f), circle(12.5f, 14f, 1.75f))

    private fun circle(
        cx: Float,
        cy: Float,
        r: Float,
    ): String = "M${cx - r} ${cy}a$r $r 0 1 0 ${2 * r} 0a$r $r 0 1 0 ${-2 * r} 0z"

    private fun rect(
        x: Float,
        y: Float,
        w: Float,
        h: Float,
        r: Float,
    ): String =
        "M${x + r} ${y}h${w - 2 * r}a$r $r 0 0 1 $r ${r}v${h - 2 * r}a$r $r 0 0 1 ${-r} ${r}" +
            "h${-(w - 2 * r)}a$r $r 0 0 1 ${-r} ${-r}v${-(h - 2 * r)}a$r $r 0 0 1 $r ${-r}z"

    private fun icon(
        name: String,
        vararg paths: String,
    ): ImageVector {
        assert(paths.isNotEmpty()) { "icon $name has at least one path" }
        val builder =
            ImageVector.Builder(
                name = name,
                defaultWidth = 20.dp,
                defaultHeight = 20.dp,
                viewportWidth = 20f,
                viewportHeight = 20f,
            )
        for (data in paths) {
            builder.addPath(
                pathData = PathParser().parsePathString(data).toNodes(),
                stroke = SolidColor(Color.Black),
                strokeLineWidth = 1.5f,
                strokeLineCap = StrokeCap.Round,
                strokeLineJoin = StrokeJoin.Round,
            )
        }
        return builder.build()
    }
}
