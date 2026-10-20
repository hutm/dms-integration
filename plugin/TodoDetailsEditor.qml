import QtQuick
import QtQuick.Controls
import qs.Common
import qs.Widgets
import "."
import "TodoFields.js" as Fields

Column {
    id: editor
    objectName: "todoDetailsEditor"
    spacing: Theme.spacingS
    property alias dueDate: dateInput.text
    property alias notes: notesInput.text
    readonly property bool valid: Fields.validDate(dueDate) && Fields.notesLength(notes) <= 8192
    property bool calendarOpen: false
    property date displayedMonth: new Date()
    signal saveRequested()

    function load(date, text) {
        dueDate = date || ""
        notes = text || ""
        displayedMonth = Fields.parseDate(dueDate) || new Date()
        calendarOpen = false
    }
    function shiftMonth(offset) {
        displayedMonth = new Date(displayedMonth.getFullYear(), displayedMonth.getMonth() + offset, 1, 12)
    }

    StyledText { text: "Date"; color: Theme.surfaceVariantText; font.pixelSize: Theme.fontSizeSmall }
    Row {
        width: parent.width
        spacing: Theme.spacingS
        DankTextField {
            id: dateInput
            objectName: "todoDateInput"
            width: parent.width - calendarButton.width - clearButton.width - Theme.spacingS * 2
            placeholderText: "No date · YYYY-MM-DD"
            maximumLength: 10
            onAccepted: if (editor.valid) editor.saveRequested()
        }
        TodoButton {
            id: calendarButton
            text: "Calendar"
            onClicked: editor.calendarOpen = !editor.calendarOpen
        }
        TodoButton {
            id: clearButton
            text: "Clear"
            enabled: dateInput.text.length > 0
            onClicked: { dateInput.text = ""; editor.calendarOpen = false }
        }
    }
    StyledText {
        visible: !Fields.validDate(editor.dueDate)
        text: "Choose a valid date or leave it empty."
        color: Theme.error
        font.pixelSize: Theme.fontSizeSmall
    }
    Column {
        width: parent.width
        visible: editor.calendarOpen
        spacing: Theme.spacingXS
        Row {
            width: parent.width
            TodoButton { text: "‹"; width: 42; onClicked: editor.shiftMonth(-1) }
            StyledText {
                width: parent.width - 84
                height: 36
                text: Qt.formatDate(editor.displayedMonth, "MMMM yyyy")
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                color: Theme.surfaceText
            }
            TodoButton { text: "›"; width: 42; onClicked: editor.shiftMonth(1) }
        }
        DayOfWeekRow {
            width: parent.width
            locale: Qt.locale()
            delegate: StyledText {
                required property string shortName
                text: shortName
                color: Theme.surfaceVariantText
                font.pixelSize: Theme.fontSizeSmall
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
        }
        MonthGrid {
            id: calendar
            objectName: "todoCalendar"
            width: parent.width
            height: 196
            month: editor.displayedMonth.getMonth()
            year: editor.displayedMonth.getFullYear()
            locale: Qt.locale()
            delegate: Rectangle {
                required property var model
                implicitWidth: 32
                implicitHeight: 28
                radius: 14
                color: Fields.formatDate(model.date) === editor.dueDate ? Theme.primary : "transparent"
                StyledText {
                    anchors.centerIn: parent
                    text: parent.model.day
                    color: Fields.formatDate(parent.model.date) === editor.dueDate ? Theme.onPrimary : Theme.surfaceText
                    opacity: parent.model.month === calendar.month ? 1 : 0.4
                }
            }
            onClicked: date => {
                dateInput.text = Fields.formatDate(date)
                editor.calendarOpen = false
            }
        }
    }
    StyledText { text: "Notes"; color: Theme.surfaceVariantText; font.pixelSize: Theme.fontSizeSmall }
    ScrollView {
        width: parent.width
        height: 96
        TextArea {
            id: notesInput
            objectName: "todoNotesInput"
            placeholderText: "Add notes…"
            wrapMode: TextEdit.Wrap
            selectByMouse: true
            color: Theme.surfaceText
            placeholderTextColor: Theme.surfaceVariantText
            font.pixelSize: Theme.fontSizeMedium
            padding: Theme.spacingS
            background: Rectangle {
                color: Theme.surfaceContainerHigh
                radius: Theme.cornerRadius / 2
                border.color: notesInput.activeFocus ? Theme.primary : Theme.outlineVariant
                border.width: 1
            }
        }
    }
    StyledText {
        visible: Fields.notesLength(editor.notes) > 8192
        text: "Notes can contain up to 8192 characters."
        color: Theme.error
        font.pixelSize: Theme.fontSizeSmall
    }
    TodoButton {
        objectName: "todoSaveDetails"
        text: "Save changes"
        enabled: editor.valid
        onClicked: editor.saveRequested()
    }
}
