import QtQuick
import org.kde.plasma.plasmoid

WallpaperItem {
    id: root
    property string endpoint: configuration.Endpoint || ""
    property int sequence: -1
    property bool requesting: false
    property int front: 0
    property var pendingRequest: null

    // Two images keep the previous frame visible while the next one decodes.
    Image {
        id: first
        anchors.fill: parent
        visible: root.front === 0
        cache: false
        asynchronous: true
        fillMode: Image.Stretch
        onStatusChanged: if (status === Image.Ready) root.front = 0
    }
    Image {
        id: second
        anchors.fill: parent
        visible: root.front === 1
        cache: false
        asynchronous: true
        fillMode: Image.Stretch
        onStatusChanged: if (status === Image.Ready) root.front = 1
    }

    function inspectFrame() {
        if (!endpoint || requesting || first.status === Image.Loading || second.status === Image.Loading) {
            return;
        }
        requesting = true;
        const request = new XMLHttpRequest();
        pendingRequest = request;
        watchdog.restart();
        request.onreadystatechange = function() {
            if (request.readyState !== XMLHttpRequest.DONE) return;
            watchdog.stop();
            root.requesting = false;
            root.pendingRequest = null;
            if (request.status !== 200) return;
            try {
                const state = JSON.parse(request.responseText);
                if (state.sequence === root.sequence || state.sequence < 0) return;
                root.sequence = state.sequence;
                const next = root.front === 0 ? second : first;
                next.source = root.endpoint + "/frame.jpg?sequence=" + state.sequence;
                root.loading = false;
            } catch (error) {
                console.warn("Drape wallpaper returned invalid frame metadata");
            }
        };
        request.open("GET", endpoint + "/status");
        request.send();
    }

    Timer {
        interval: 50
        repeat: true
        running: root.endpoint.length > 0
        onTriggered: root.inspectFrame()
    }
    Timer {
        id: watchdog
        interval: 2000
        onTriggered: {
            if (root.pendingRequest) root.pendingRequest.abort();
            root.pendingRequest = null;
            root.requesting = false;
            root.loading = false;
        }
    }
    onEndpointChanged: { sequence = -1; inspectFrame(); }
    Component.onCompleted: { loading = false; inspectFrame(); }
    Component.onDestruction: if (pendingRequest) pendingRequest.abort()
}
