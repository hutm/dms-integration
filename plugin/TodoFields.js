.pragma library

function parseDate(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return null
    const parts = value.split("-").map(Number)
    if (parts[0] < 1) return null
    const date = new Date(2000, 0, 1, 12)
    date.setFullYear(parts[0], parts[1] - 1, parts[2])
    return date.getFullYear() === parts[0] && date.getMonth() === parts[1] - 1
        && date.getDate() === parts[2] ? date : null
}

function validDate(value) { return value === "" || parseDate(value) !== null }

function formatDate(date) {
    return String(date.getFullYear()).padStart(4, "0") + "-"
        + String(date.getMonth() + 1).padStart(2, "0") + "-"
        + String(date.getDate()).padStart(2, "0")
}

function mergeDetails(current, draft, base) {
    const result = {}
    for (const key of ["text", "dueDate", "notes"]) {
        const previous = base[key] || ""
        const existing = current[key] || ""
        const edited = draft[key] || ""
        if (edited !== previous && existing !== previous && edited !== existing)
            return null
        result[key] = edited === previous ? existing : edited
    }
    return result
}

function notesLength(value) { return Array.from(value || "").length }
