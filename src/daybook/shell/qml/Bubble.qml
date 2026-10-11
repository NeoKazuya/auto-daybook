// Speech-bubble overlay (README.md, "Speech bubble"). Created once and kept hidden;
// Python places and shows it. It never takes keyboard focus and Esc isn't bound:
// only its buttons or the countdown dismiss it. All text is plain text.
import QtQuick
import QtQuick.Window

Window {
    id: win
    objectName: "bubbleWindow"
    visible: false
    title: "Daybook bubble"
    // A managed tool window (not override-redirect), so Sense sees it and masks it.
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
           | Qt.WindowDoesNotAcceptFocus
    color: "transparent"
    width: card.width + tailSize + markSize
    height: Math.max(card.height, markSize)

    // Nocturne Folio tokens, matching src/daybook/web/static/css/daybook.css.
    readonly property color paper: "#1e1f22"
    readonly property color paperLine: "#383c44"
    readonly property color ink: "#ffffff"
    readonly property color ink2: "#b5b5b5"
    readonly property color ember: "#d97757"
    readonly property color emberSoft: "#ffb59e"
    readonly property color emberInk: "#1a0a04"
    readonly property bool isAlert: overlay.alert
    readonly property real unit: Math.max(Screen.height, 600) / 1080
    readonly property real tailSize: 14 * unit
    readonly property real markSize: 44 * unit

    Item {
        id: content
        anchors.fill: parent

        NumberAnimation on opacity {
            id: fadeIn
            from: 0; to: 1; duration: 180
            easing.type: Easing.OutCubic
            running: false
        }

        Rectangle {
            id: card
            objectName: "bubbleCard"
            width: 440 * win.unit
            height: column.implicitHeight + 2 * column.padding
            radius: 16 * win.unit
            color: win.paper
            border.width: Math.max(1, win.unit)
            border.color: win.isAlert ? win.ember : win.paperLine

            Column {
                id: column
                width: parent.width
                padding: 22 * win.unit
                spacing: 12 * win.unit

                Text {
                    text: "daybook"
                    textFormat: Text.PlainText
                    font.family: overlay.monoFamily
                    font.pixelSize: 15 * win.unit
                    font.letterSpacing: 0.14 * 15 * win.unit
                    font.capitalization: Font.AllUppercase
                    color: win.isAlert ? win.emberSoft : win.ink2
                }

                Text {
                    objectName: "bubbleText"
                    width: parent.width - 2 * parent.padding
                    text: overlay.bubbleText
                    textFormat: Text.PlainText
                    wrapMode: Text.WordWrap
                    maximumLineCount: 6
                    elide: Text.ElideRight
                    font.family: overlay.serifFamily
                    font.pixelSize: 22 * win.unit
                    lineHeight: 1.35
                    color: win.ink
                }

                Text {
                    objectName: "bubbleCountdown"
                    text: "goes away in " + overlay.countdown
                    textFormat: Text.PlainText
                    font.family: overlay.monoFamily
                    font.pixelSize: 15 * win.unit
                    color: win.ink2
                }

                Row {
                    anchors.right: parent.right
                    anchors.rightMargin: parent.padding
                    spacing: 10 * win.unit

                    Rectangle {
                        id: later
                        objectName: "bubbleLater"
                        width: laterText.width + 32 * win.unit
                        height: laterText.implicitHeight + 18 * win.unit
                        radius: 4 * win.unit
                        color: laterMouse.containsMouse ? win.paperLine : "transparent"
                        border.width: Math.max(1, win.unit)
                        border.color: win.paperLine

                        Text {
                            id: laterText
                            anchors.centerIn: parent
                            text: "Later"
                            textFormat: Text.PlainText
                            font.family: overlay.monoFamily
                            font.pixelSize: 16 * win.unit
                            color: win.ink
                        }

                        MouseArea {
                            id: laterMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: overlay.dismiss()
                        }
                    }

                    Rectangle {
                        objectName: "bubbleDismiss"
                        width: dismissText.width + 32 * win.unit
                        height: dismissText.implicitHeight + 18 * win.unit
                        radius: 4 * win.unit
                        color: win.isAlert ? (dismissMouse.containsMouse ? "#c5684a" : win.ember)
                                         : (dismissMouse.containsMouse ? "#e5e5e5" : win.ink)

                        Text {
                            id: dismissText
                            anchors.centerIn: parent
                            width: Math.min(implicitWidth, card.width * 0.5)
                            elide: Text.ElideRight
                            text: overlay.dismissLabel
                            textFormat: Text.PlainText
                            font.family: overlay.monoFamily
                            font.pixelSize: 16 * win.unit
                            font.weight: Font.Bold
                            color: win.isAlert ? win.emberInk : "#000000"
                        }

                        MouseArea {
                            id: dismissMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: overlay.dismiss()
                        }
                    }
                }
            }
        }

        // The tail points from the bubble to the Daybook mark in the corner.
        Canvas {
            id: tail
            x: card.width - 1
            y: 22 * win.unit
            width: win.tailSize + 1
            height: 2 * win.tailSize
            onPaint: {
                const ctx = getContext("2d")
                ctx.reset()
                ctx.fillStyle = win.paper
                ctx.strokeStyle = card.border.color
                ctx.lineWidth = card.border.width
                ctx.beginPath()
                ctx.moveTo(0, 0)
                ctx.lineTo(width, height / 2)
                ctx.lineTo(0, height)
                ctx.fill()
                ctx.stroke()
            }
            Connections {
                target: win
                function onIsAlertChanged() { tail.requestPaint() }
            }
        }

        Rectangle {
            objectName: "bubbleMark"
            x: card.width + win.tailSize
            width: win.markSize
            height: win.markSize
            radius: width / 2
            color: win.ember

            Text {
                anchors.centerIn: parent
                text: "D"
                font.family: overlay.serifFamily
                font.pixelSize: 24 * win.unit
                font.weight: Font.Medium
                color: win.emberInk
            }
        }
    }

    Connections {
        target: overlay
        function onShownChanged() {
            if (overlay.bubble)
                fadeIn.restart()
        }
    }
}
