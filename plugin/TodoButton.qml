import QtQuick
import QtQuick.Controls
import qs.Common
import qs.Widgets

Button {
    id: button
    implicitWidth: Math.max(36, contentItem.implicitWidth + leftPadding + rightPadding)
    implicitHeight: 36
    leftPadding: Theme.spacingM
    rightPadding: Theme.spacingM
    opacity: enabled ? 1 : 0.45
    contentItem: StyledText {
        text: button.text
        font.pixelSize: Theme.fontSizeSmall
        color: button.down ? Theme.onPrimary : Theme.primary
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        radius: Theme.cornerRadius / 2
        color: button.down ? Theme.primary : button.hovered ? Theme.surfaceContainerHighest : Theme.surfaceContainerHigh
        border.width: button.activeFocus ? 2 : 1
        border.color: button.activeFocus ? Theme.primary : Theme.outlineVariant
    }
}
