import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

ApplicationWindow {
    id: root
    visible: true
    width: 1280
    height: 800
    title: "e-CALLISTO FITS Analyzer"

    header: ToolBar {
        RowLayout {
            anchors.fill: parent
            spacing: 12

            ToolButton {
                text: "☰"
                onClicked: navDrawer.open()
            }

            Label {
                text: "e-CALLISTO FITS Analyzer"
                font.pixelSize: 20
                font.bold: true
                Layout.fillWidth: true
            }

            ToolButton {
                text: "Launch Analyzer"
                onClicked: appController.openAnalyzer()
            }
        }
    }

    Drawer {
        id: navDrawer
        width: Math.min(root.width * 0.75, 360)
        height: root.height

        ColumnLayout {
            anchors.fill: parent
            spacing: 12
            padding: 16

            Label {
                text: "Navigation"
                font.pixelSize: 18
                font.bold: true
            }

            Button {
                text: "FITS Analyzer"
                icon.name: "view-dashboard"
                onClicked: {
                    navDrawer.close()
                    appController.openAnalyzer()
                }
            }

            Button {
                text: "Downloader"
                icon.name: "folder-download"
                onClicked: {
                    navDrawer.close()
                    appController.openDownloader()
                }
            }

            Button {
                text: "GOES XRS Monitor"
                icon.name: "chart-line"
                onClicked: {
                    navDrawer.close()
                    appController.openGoesXrs()
                }
            }

            Button {
                text: "SOHO/LASCO Viewer"
                icon.name: "image-multiple"
                onClicked: {
                    navDrawer.close()
                    appController.openLascoViewer()
                }
            }

            Item {
                Layout.fillHeight: true
            }

            Button {
                text: "Quit"
                onClicked: Qt.quit()
            }
        }
    }

    ScrollView {
        anchors.fill: parent
        contentWidth: contentItem.implicitWidth
        contentHeight: contentItem.implicitHeight

        ColumnLayout {
            width: parent.width
            spacing: 24
            padding: 24

            Rectangle {
                Layout.fillWidth: true
                color: "#1f2937"
                radius: 16
                height: 160

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 20
                    spacing: 8

                    Label {
                        text: "Modern analysis workspace"
                        color: "white"
                        font.pixelSize: 22
                        font.bold: true
                    }

                    Label {
                        text: "Launch the analyzer or supporting tools to explore FITS data, download archives, and review space weather context."
                        color: "#d1d5db"
                        wrapMode: Text.WordWrap
                    }

                    RowLayout {
                        spacing: 12
                        Button {
                            text: "Open Analyzer"
                            onClicked: appController.openAnalyzer()
                        }
                        Button {
                            text: "Open Downloader"
                            onClicked: appController.openDownloader()
                        }
                    }
                }
            }

            GridLayout {
                columns: root.width > 960 ? 2 : 1
                columnSpacing: 20
                rowSpacing: 20
                Layout.fillWidth: true

                Frame {
                    Layout.fillWidth: true
                    padding: 16

                    ColumnLayout {
                        spacing: 12

                        Label {
                            text: "FITS Analyzer"
                            font.pixelSize: 18
                            font.bold: true
                        }
                        Label {
                            text: "Visualize e-CALLISTO FITS data with full editing, plotting, and export capabilities."
                            wrapMode: Text.WordWrap
                            color: "#4b5563"
                        }
                        Button {
                            text: "Launch"
                            onClicked: appController.openAnalyzer()
                        }
                    }
                }

                Frame {
                    Layout.fillWidth: true
                    padding: 16

                    ColumnLayout {
                        spacing: 12

                        Label {
                            text: "Downloader"
                            font.pixelSize: 18
                            font.bold: true
                        }
                        Label {
                            text: "Search stations, preview files, and download FITS datasets quickly."
                            wrapMode: Text.WordWrap
                            color: "#4b5563"
                        }
                        Button {
                            text: "Launch"
                            onClicked: appController.openDownloader()
                        }
                    }
                }

                Frame {
                    Layout.fillWidth: true
                    padding: 16

                    ColumnLayout {
                        spacing: 12

                        Label {
                            text: "GOES XRS Monitor"
                            font.pixelSize: 18
                            font.bold: true
                        }
                        Label {
                            text: "Track X-ray flux and flare activity with the GOES XRS viewer."
                            wrapMode: Text.WordWrap
                            color: "#4b5563"
                        }
                        Button {
                            text: "Launch"
                            onClicked: appController.openGoesXrs()
                        }
                    }
                }

                Frame {
                    Layout.fillWidth: true
                    padding: 16

                    ColumnLayout {
                        spacing: 12

                        Label {
                            text: "SOHO/LASCO Viewer"
                            font.pixelSize: 18
                            font.bold: true
                        }
                        Label {
                            text: "Search CME events and access associated imagery quickly."
                            wrapMode: Text.WordWrap
                            color: "#4b5563"
                        }
                        Button {
                            text: "Launch"
                            onClicked: appController.openLascoViewer()
                        }
                    }
                }
            }
        }
    }
}
