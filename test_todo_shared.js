const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const plugin = process.argv[2] || path.join(__dirname, 'plugin');
const js = fs.readFileSync(path.join(plugin, 'TodoShared.js'), 'utf8').replace(/^\.pragma library\s*/m, '');
const shared = vm.createContext({});
vm.runInContext(js, shared);
function widget(path) { return {storageFilePath: path, todos: [], lists: [{id:'default', title:'Dank Todo'}], revision: 0}; }
const a = widget('/tasks'), b = widget('/tasks'), c = widget('/other');
shared.load(a, [{id:'parent'}]);
shared.load(b, []);
assert.equal(b.todos, a.todos, 'a stale asynchronous load must use the current shared model');
a.todos = [...a.todos, {id:'child', parentId:'parent'}];
shared.publish(a);
assert.equal(b.todos, a.todos, 'edits on one monitor must immediately reach the IPC monitor');
const d = widget('/tasks');
shared.load(d, [{id:'parent'}]);
assert.equal(d.todos.length, 2, 'new monitor must not drop existing children');
b.todos = b.todos.map(t => t.id === 'child' ? {...t, deletedAt: 'now'} : t);
shared.publish(b);
assert.equal(a.todos[1].deletedAt, 'now');
assert.equal(d.todos[1].deletedAt, 'now');
shared.load(c, [{id:'independent'}]);
assert.equal(c.todos[0].id, 'independent');
shared.detach(a);shared.detach(b);
assert.equal(shared.models['/tasks'].widgets.length, 1);
shared.detach(d);
assert.equal(shared.models['/tasks'], undefined);
shared.load(d, [{id:'disk-after-restart'}]);
assert.equal(d.todos[0].id, 'disk-after-restart');
console.log('Shared model regression checks passed');

const fields = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(plugin, 'TodoFields.js'), 'utf8').replace(/^\.pragma library\s*/m, ''), fields);
for (const value of ['', '2026-10-19', '2028-02-29', '0001-01-01', '9999-12-31']) assert.equal(fields.validDate(value), true, value);
for (const value of ['2026-02-29', '2026-04-31', '2026-13-01', '2026-00-01', '2026-01-00', '20261019', '2026-1-1', '0000-01-01']) assert.equal(fields.validDate(value), false, value);
assert.equal(fields.formatDate(fields.parseDate('2026-03-08')), '2026-03-08', 'calendar dates must not shift with timezone/DST');
console.log('Date validation and timezone checks passed');

const base = {text: 'Task', dueDate: '', notes: 'Original'};
const current = {...base, notes: 'Remote edit'};
const draft = {...base, dueDate: '2026-11-02'};
const merged = fields.mergeDetails(current, draft, base);
assert.equal(merged.notes, 'Remote edit', 'saving a date from an open form must preserve incoming notes');
assert.equal(merged.dueDate, '2026-11-02');
assert.equal(fields.mergeDetails(current, {...draft, notes:'Local edit'}, base), null, 'a stale form must not overwrite a conflicting incoming edit');
console.log('Open-editor concurrent edit checks passed');

assert.equal(fields.notesLength('🗒'.repeat(8192)), 8192, 'Unicode note limits count characters rather than UTF-16 code units');

const e = widget('/lists'), f = widget('/lists');
shared.load(e, [{id:'a', listId:'default'}], [{id:'default', title:'Original'}, {id:'work', title:'Work'}]);
shared.load(f, [], [{id:'default', title:'Stale'}]);
assert.equal(f.lists, e.lists, 'late monitor initialization must use current list metadata');
e.lists = e.lists.map(list => list.id === 'work' ? {...list, title:'Renamed remotely'} : list);
shared.publish(e);
assert.equal(f.lists[1].title, 'Renamed remotely', 'tab names must update across all monitors');
assert.equal(f.todos, e.todos);

const lists = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(plugin, 'TodoLists.js'), 'utf8').replace(/^\.pragma library\s*/m, ''), lists);
const document = {lists:[{id:'a', title:'Personal'}, {id:'b', title:'Work'}], todos:[
  {id:'parent', listId:'a'}, {id:'child', listId:'a', parentId:'parent'}, {id:'other', listId:'b'}
]};
assert.equal(lists.validDocument(document), true);
assert.equal(lists.validDocument({...document, lists:[...document.lists, {id:'a', title:'Duplicate'}]}), false);
assert.equal(lists.validDocument({...document, todos:[...document.todos, {id:'cross', listId:'b', parentId:'parent'}]}), false, 'cross-list parents must be rejected');
assert.equal(lists.validDocument({...document, todos:[{id:'x', listId:'unknown'}]}), false);
assert.equal(lists.validDocument({...document, todos:[{id:'x', listId:'a', parentId:'y'}, {id:'y', listId:'a', parentId:'x'}]}), false);

// Execute the actual QML model methods to check the tab-scoped destructive action.
const qml = fs.readFileSync(path.join(plugin, 'DankTodoWidget.qml'), 'utf8');
const model = vm.createContext({todos:[
  {id:'a', listId:'personal', completed:true}, {id:'b', listId:'work', completed:true},
  {id:'c', listId:'personal', completed:false}
], activeListId:'personal', revision:0, saveTodos() {}});
const clearDone = qml.match(/    function clearCompleted\(\) \{[\s\S]*?\n    \}/)[0];
vm.runInContext(clearDone + '\nclearCompleted()', model);
assert.ok(model.todos[0].deletedAt);
assert.equal(model.todos[1].deletedAt, undefined, 'Clear done must not delete completed tasks in another tab');
assert.equal(model.todos[2].deletedAt, undefined);
console.log('Multiple-list isolation, shared tabs and payload validation checks passed');
