.pragma library

// A bar widget is instantiated once per monitor. Keep one canonical task model
// per storage path so a stale widget cannot overwrite another monitor's edits.
var models = Object.create(null)

function attach(widget, rows) {
    const path = widget.storageFilePath
    if (!models[path])
        models[path] = {todos: rows, widgets: []}
    const model = models[path]
    if (model.widgets.indexOf(widget) === -1)
        model.widgets.push(widget)
    return model
}

function load(widget, diskRows) {
    const model = attach(widget, diskRows)
    // Another instance may have saved while this asynchronous read was pending.
    // Its in-memory model is newer than the file read and remains authoritative.
    widget.todos = model.todos
    widget.revision++
}

function publish(widget) {
    const model = attach(widget, widget.todos)
    model.todos = widget.todos
    for (const peer of model.widgets) {
        if (peer !== widget) {
            peer.todos = model.todos
            peer.revision++
        }
    }
}

function detach(widget) {
    for (const path in models) {
        const model = models[path]
        model.widgets = model.widgets.filter(peer => peer !== widget)
        if (!model.widgets.length)
            delete models[path]
    }
}
