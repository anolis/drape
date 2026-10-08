import QtQuick
import QtQuick.Controls

Item {
    property string cfg_Endpoint
    property string cfg_Lease
    implicitWidth: notice.implicitWidth
    implicitHeight: notice.implicitHeight
    Label {
        id: notice
        width: parent.width
        wrapMode: Text.WordWrap
        text: qsTr("Start and control playback in Drape’s Live wallpapers section. Stop or Restore previous Plasma wallpapers returns each screen to its original wallpaper. You can also choose another wallpaper type here to stop Drape from controlling this screen.")
    }
}
