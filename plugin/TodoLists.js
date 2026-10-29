.pragma library

function validDocument(document) {
    if (!document || !Array.isArray(document.lists) || !document.lists.length || !Array.isArray(document.todos))
        return false
    const lists = new Set()
    for (const list of document.lists) {
        if (!list || typeof list.id !== "string" || !list.id || lists.has(list.id)
            || typeof list.title !== "string" || !list.title.trim() || list.title.length > 1024)
            return false
        lists.add(list.id)
    }
    const rows = new Map()
    for (const row of document.todos) {
        if (!row || typeof row.id !== "string" || !row.id || rows.has(row.id) || !lists.has(row.listId))
            return false
        rows.set(row.id, row)
    }
    for (const row of document.todos) {
        const seen = new Set([row.id])
        let parent = row.parentId
        while (parent) {
            const ancestor = rows.get(parent)
            if (!ancestor || ancestor.listId !== row.listId || seen.has(parent)) return false
            seen.add(parent)
            parent = ancestor.parentId
        }
    }
    return true
}
