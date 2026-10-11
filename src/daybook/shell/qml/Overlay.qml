// Full-screen overlay, full-bleed layout (README.md). Created once and kept hidden;
// Python shows it. All text is plain text: chat-made skills write it.
import QtQuick
import QtQuick.Window

Window {
    id: win
    objectName: "overlayWindow"
    visible: false
    title: "Daybook overlay"
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    color: "transparent"

    // Nocturne Folio tokens, matching src/daybook/web/static/css/daybook.css.
    readonly property color desk: "#0d0e10"
    readonly property color ink: "#ffffff"
    readonly property color inkSoft: "#e5e5e5"
    readonly property color ink2: "#b5b5b5"
    readonly property color ink3: "#8a8a8a"
    readonly property color ember: "#d97757"
    readonly property color emberSoft: "#ffb59e"
    readonly property color emberInk: "#1a0a04"
    readonly property bool isAlert: overlay.alert
    // Sizes follow the screen height so 1080p lands near the agreed 64 px title.
    readonly property real unit: Math.max(height, 600) / 1080
    // Skill text has no length limit, so the message gets whatever height is left
    // after the controls; the countdown and button always stay on screen.
    readonly property real margin: height * 0.08

    Rectangle {
        id: backdrop
        objectName: "backdrop"
        anchors.fill: parent
        color: Qt.rgba(win.desk.r, win.desk.g, win.desk.b, 0.9)
        opacity: 1

        NumberAnimation on opacity {
            id: fadeIn
            from: 0; to: 1; duration: 180
            easing.type: Easing.OutCubic
            running: false
        }

        Column {
            id: content
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: parent.width * 0.09
            width: Math.min(parent.width * 0.62, 1200 * win.unit)
            spacing: 22 * win.unit
            readonly property real bodyRoom: win.height - 2 * win.margin - labelText.height
                - titleText.height - controls.height - 3 * spacing

            Text {
                id: labelText
                objectName: "label"
                text: overlay.label
                textFormat: Text.PlainText
                font.family: overlay.monoFamily
                font.pixelSize: 18 * win.unit
                font.letterSpacing: 0.14 * 18 * win.unit
                font.capitalization: Font.AllUppercase
                color: win.isAlert ? win.emberSoft : win.ink2
                visible: text.length > 0
            }

            Text {
                id: titleText
                objectName: "title"
                width: parent.width
                text: overlay.title
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                maximumLineCount: 3
                elide: Text.ElideRight
                font.family: overlay.serifFamily
                font.pixelSize: 64 * win.unit
                font.weight: Font.Medium
                lineHeight: 1.1
                color: win.ink
            }

            Text {
                id: bodyText
                objectName: "body"
                width: parent.width
                text: overlay.body
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                maximumLineCount: Math.max(1, Math.floor(content.bodyRoom / (bodyMetrics.height * lineHeight)))
                elide: Text.ElideRight
                font.family: overlay.serifFamily
                font.pixelSize: 28 * win.unit
                lineHeight: 1.5
                color: win.inkSoft
                visible: text.length > 0
            }

            FontMetrics {
                id: bodyMetrics
                font: bodyText.font
            }

            Row {
                id: controls
                spacing: 32 * win.unit
                topPadding: 12 * win.unit

                Text {
                    objectName: "countdown"
                    anchors.verticalCenter: parent.verticalCenter
                    text: overlay.countdown
                    font.family: overlay.monoFamily
                    font.pixelSize: 52 * win.unit
                    font.letterSpacing: 0.04 * 52 * win.unit
                    color: win.isAlert ? win.emberSoft : win.ink
                }

                Rectangle {
                    id: button
                    objectName: "dismissButton"
                    anchors.verticalCenter: parent.verticalCenter
                    width: buttonText.width + 48 * win.unit
                    height: buttonText.implicitHeight + 28 * win.unit
                    radius: 4 * win.unit
                    color: win.isAlert ? (mouse.containsMouse ? "#c5684a" : win.ember)
                                     : (mouse.containsMouse ? "#e5e5e5" : win.ink)
                    activeFocusOnTab: true
                    Keys.onReturnPressed: overlay.dismiss()
                    Keys.onSpacePressed: overlay.dismiss()
                    border.width: activeFocus ? 2 * win.unit : 0
                    border.color: win.emberSoft

                    Text {
                        id: buttonText
                        anchors.centerIn: parent
                        width: Math.min(implicitWidth, content.width * 0.45)
                        elide: Text.ElideRight
                        text: overlay.dismissLabel
                        textFormat: Text.PlainText
                        font.family: overlay.monoFamily
                        font.pixelSize: 22 * win.unit
                        font.weight: Font.Bold
                        font.letterSpacing: 0.05 * 22 * win.unit
                        color: win.isAlert ? win.emberInk : "#000000"
                    }

                    MouseArea {
                        id: mouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: overlay.dismiss()
                    }
                }

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "or press Esc"
                    font.family: overlay.monoFamily
                    font.pixelSize: 18 * win.unit
                    color: win.ink3
                }
            }
        }
    }

    Shortcut {
        sequence: "Esc"
        context: Qt.WindowShortcut
        onActivated: overlay.dismiss()
    }

    Connections {
        target: overlay
        function onShownChanged() {
            if (overlay.shown) {
                fadeIn.restart()
                button.forceActiveFocus()
            }
        }
    }
}
