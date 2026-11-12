package dev.msj.journal.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.PlainTooltip
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TooltipAnchorPosition
import androidx.compose.material3.TooltipBox
import androidx.compose.material3.TooltipDefaults
import androidx.compose.material3.rememberTooltipState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import dev.msj.journal.ui.theme.Journal
import dev.msj.journal.ui.theme.JournalShapes
import dev.msj.journal.ui.theme.Space

/** The page's one primary action: filled with the accent. */
@Composable
fun PrimaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    icon: ImageVector? = null,
) {
    val c = Journal.colors
    Button(
        onClick = onClick,
        modifier = modifier.heightIn(min = Space.control),
        enabled = enabled,
        shape = JournalShapes.control,
        colors =
            ButtonDefaults.buttonColors(
                containerColor = c.accent,
                contentColor = c.accentInk,
                disabledContainerColor = c.rule,
                disabledContentColor = c.inkDisabled,
            ),
        contentPadding = PaddingValues(horizontal = Space.s4, vertical = Space.s2),
    ) {
        ButtonContent(text, icon)
    }
}

/** Any other action: a bounded outline so it reads as a control in its idle state. */
@Composable
fun SecondaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    icon: ImageVector? = null,
    danger: Boolean = false,
) {
    val c = Journal.colors
    val ink = if (danger) c.stFailed else c.ink
    OutlinedButton(
        onClick = onClick,
        modifier = modifier.heightIn(min = Space.control),
        enabled = enabled,
        shape = JournalShapes.control,
        border = BorderStroke(Space.hairline, if (enabled) c.fieldBorder else c.rule),
        colors =
            ButtonDefaults.outlinedButtonColors(
                containerColor = c.surface,
                contentColor = ink,
                disabledContainerColor = c.fieldReadonly,
                disabledContentColor = c.inkDisabled,
            ),
        contentPadding = PaddingValues(horizontal = Space.s4, vertical = Space.s2),
    ) {
        ButtonContent(text, icon)
    }
}

/** A low-emphasis inline action (Show details, Retry inside a notice). Still bounded by its text color and underline-free padding. */
@Composable
fun InlineAction(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    val c = Journal.colors
    TextButton(
        onClick = onClick,
        modifier = modifier.heightIn(min = 40.dp),
        enabled = enabled,
        shape = JournalShapes.control,
        colors = ButtonDefaults.textButtonColors(contentColor = c.accent, disabledContentColor = c.inkDisabled),
        contentPadding = PaddingValues(horizontal = Space.s2, vertical = Space.s1),
    ) {
        Text(text, style = Journal.type.uiStrong)
    }
}

@Composable
private fun RowScope.ButtonContent(
    text: String,
    icon: ImageVector?,
) {
    if (icon != null) {
        Icon(icon, contentDescription = null, modifier = Modifier.size(20.dp))
        Spacer(Modifier.width(Space.s2))
    }
    Text(text, style = Journal.type.uiStrong, maxLines = 1, overflow = TextOverflow.Ellipsis)
}

/**
 * An icon-only control. [label] is both its accessible name and the tooltip
 * shown on long press, so nothing is left unexplained.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun IconAction(
    icon: ImageVector,
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    tint: androidx.compose.ui.graphics.Color = Journal.colors.ink2,
) {
    val c = Journal.colors
    TooltipBox(
        positionProvider = TooltipDefaults.rememberTooltipPositionProvider(TooltipAnchorPosition.Below),
        tooltip = { PlainTooltip { Text(label, style = Journal.type.uiSmall) } },
        state = rememberTooltipState(),
    ) {
        IconButton(
            onClick = onClick,
            modifier = modifier.size(Space.control),
            enabled = enabled,
            colors = IconButtonDefaults.iconButtonColors(contentColor = tint, disabledContentColor = c.inkDisabled),
        ) {
            Icon(icon, contentDescription = label, modifier = Modifier.size(22.dp))
        }
    }
}

/**
 * The light/dark switch: a circular button at the top right showing the mode
 * it switches TO (a sun while dark, a moon while light).
 */
@Composable
fun ThemeSwitch(
    dark: Boolean,
    onToggle: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val c = Journal.colors
    val label = if (dark) "Switch to light mode" else "Switch to dark mode"
    Box(
        modifier
            .size(Space.control)
            .padding(6.dp)
            .border(Space.hairline, c.ruleStrong, CircleShape),
        contentAlignment = Alignment.Center,
    ) {
        IconAction(
            icon = if (dark) JournalIcons.Sun else JournalIcons.Moon,
            label = label,
            onClick = onToggle,
            modifier = Modifier.testTag(TestTags.THEME_SWITCH),
        )
    }
}

/**
 * The page's top bar: pinned under the status bar, title at the left, actions
 * at the right, a hairline rule below.
 */
@Composable
fun TopBar(
    title: String,
    modifier: Modifier = Modifier,
    navigation: (@Composable () -> Unit)? = null,
    subtitle: String? = null,
    actions: @Composable RowScope.() -> Unit = {},
) {
    val c = Journal.colors
    Row(
        modifier
            .fillMaxWidth()
            .background(c.bg)
            .windowInsetsPadding(WindowInsets.statusBars)
            .height(56.dp)
            .bottomRule(c.rule)
            .padding(start = if (navigation == null) Space.gutter else Space.s1, end = Space.s1),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (navigation != null) navigation()
        androidx.compose.foundation.layout.Column(Modifier.weight(1f)) {
            Text(title, style = Journal.type.barTitle, color = c.ink, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (subtitle != null) {
                Text(
                    subtitle,
                    style = Journal.type.monoSmall,
                    color = c.ink3,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.End, content = actions)
    }
}

fun Modifier.bottomRule(color: androidx.compose.ui.graphics.Color): Modifier =
    drawBehind {
        val y = size.height - 0.5f.dp.toPx()
        drawLine(color, Offset(0f, y), Offset(size.width, y), Space.hairline.toPx())
    }

fun Modifier.topRule(color: androidx.compose.ui.graphics.Color): Modifier =
    drawBehind {
        drawLine(color, Offset(0f, 0f), Offset(size.width, 0f), Space.hairline.toPx())
    }

/** A hairline rule between blocks (a ruled line, not a card edge). */
@Composable
fun Rule(
    modifier: Modifier = Modifier,
    strong: Boolean = false,
) {
    val c = Journal.colors
    Box(
        modifier
            .fillMaxWidth()
            .height(Space.hairline)
            .background(if (strong) c.ruleStrong else c.rule),
    )
}

/** A small label above a block of content. Plain text: it neither acts nor navigates. */
@Composable
fun SectionLabel(
    text: String,
    modifier: Modifier = Modifier,
) {
    Text(text.uppercase(), style = Journal.type.label, color = Journal.colors.ink3, modifier = modifier)
}

/** A hollow chip for a tag, project or skill. */
@Composable
fun TagChip(
    text: String,
    modifier: Modifier = Modifier,
) {
    val c = Journal.colors
    Text(
        text,
        style = Journal.type.monoSmall,
        color = c.ink2,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        modifier =
            modifier
                .border(Space.hairline, c.ruleStrong, JournalShapes.small)
                .padding(horizontal = 6.dp, vertical = 2.dp),
    )
}

/**
 * A segmented choice: hairline-bounded segments, the selection shown by a
 * heavier accent border, accent text and a light tint.
 */
@Composable
fun <T> SegmentedChoice(
    options: List<T>,
    selected: T,
    label: (T) -> String,
    onSelect: (T) -> Unit,
    modifier: Modifier = Modifier,
    tag: (T) -> String = { "" },
) {
    assert(selected in options) { "the selected option is one of the options" }
    val c = Journal.colors
    Row(
        modifier
            .heightIn(min = 40.dp)
            .selectableGroup(),
        horizontalArrangement = Arrangement.spacedBy((-1).dp),
    ) {
        options.forEach { option ->
            val isSelected = option == selected
            Box(
                Modifier
                    .weight(1f)
                    .heightIn(min = 40.dp)
                    .clip(JournalShapes.control)
                    .background(if (isSelected) c.accentTint else c.surface, JournalShapes.control)
                    .border(
                        width = if (isSelected) 1.5.dp else Space.hairline,
                        color = if (isSelected) c.accent else c.fieldBorder,
                        shape = JournalShapes.control,
                    ).selectable(selected = isSelected, role = Role.Tab, onClick = { onSelect(option) })
                    .then(if (tag(option).isEmpty()) Modifier else Modifier.testTag(tag(option))),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    label(option),
                    style = if (isSelected) Journal.type.uiStrong else Journal.type.ui,
                    color = if (isSelected) c.accent else c.ink2,
                    maxLines = 1,
                )
            }
        }
    }
}

/**
 * A text field that looks editable before it is touched: persistent label,
 * bounded box on the field color, inner padding.
 */
@Composable
fun JournalField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    modifier: Modifier = Modifier,
    placeholder: String? = null,
    supporting: String? = null,
    singleLine: Boolean = true,
    minLines: Int = 1,
    textStyle: TextStyle = Journal.type.ui,
    isError: Boolean = false,
    enabled: Boolean = true,
    keyboardType: KeyboardType = KeyboardType.Text,
    visualTransformation: VisualTransformation = VisualTransformation.None,
    trailing: (@Composable () -> Unit)? = null,
) {
    val c = Journal.colors
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = modifier.fillMaxWidth(),
        enabled = enabled,
        label = { Text(label) },
        placeholder = placeholder?.let { { Text(it, style = textStyle, color = c.ink3) } },
        supportingText = supporting?.let { { Text(it, style = Journal.type.uiSmall) } },
        singleLine = singleLine,
        minLines = minLines,
        textStyle = textStyle.copy(color = c.ink),
        isError = isError,
        trailingIcon = trailing,
        visualTransformation = visualTransformation,
        keyboardOptions = KeyboardOptions(keyboardType = keyboardType),
        shape = JournalShapes.control,
        colors =
            OutlinedTextFieldDefaults.colors(
                focusedContainerColor = c.field,
                unfocusedContainerColor = c.field,
                disabledContainerColor = c.fieldReadonly,
                errorContainerColor = c.field,
                focusedBorderColor = c.accent,
                unfocusedBorderColor = c.fieldBorder,
                disabledBorderColor = c.rule,
                errorBorderColor = c.stFailed,
                focusedLabelColor = c.accent,
                unfocusedLabelColor = c.ink2,
                errorLabelColor = c.stFailed,
                cursorColor = c.accent,
                focusedSupportingTextColor = c.ink3,
                unfocusedSupportingTextColor = c.ink3,
                errorSupportingTextColor = c.stFailed,
            ),
    )
}

/** A band carrying status (queue, unsent uploads): the bg-band color with hairline rules. */
@Composable
fun Band(
    modifier: Modifier = Modifier,
    minHeight: Dp = 44.dp,
    content: @Composable RowScope.() -> Unit,
) {
    val c = Journal.colors
    Row(
        modifier
            .fillMaxWidth()
            .background(c.bgBand)
            .bottomRule(c.rule)
            .heightIn(min = minHeight)
            .padding(horizontal = Space.gutter),
        verticalAlignment = Alignment.CenterVertically,
        content = content,
    )
}
