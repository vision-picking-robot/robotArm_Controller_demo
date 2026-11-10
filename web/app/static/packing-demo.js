// Keep earlier tab-only examples available when moving user management to the server.
const storageKey = "vision-lab.packing-demo.v2";
const initial = {
  customers: [{ name: "홍길동", uid: "E2-34-08-A1" }, { name: "김서연", uid: "E2-34-08-B2" }],
  events: []
};

function load() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey));
    if (Array.isArray(saved?.customers) && Array.isArray(saved.events)) return saved;
    const previous = JSON.parse(sessionStorage.getItem("vision-lab.packing-demo.v1"));
    if (Array.isArray(previous?.customers)) return { customers: previous.customers, events: [] };
  } catch {
    // A restricted browser can still use the examples in memory.
  }
  return structuredClone(initial);
}

export const demo = load();
export const legacyUsers = demo.customers.filter(person =>
  !initial.customers.some(sample => sample.name === person.name && sample.uid === person.uid));

export function saveDemo() {
  try {
    sessionStorage.setItem(storageKey, JSON.stringify(demo));
  } catch {
    // The current tab can still use its in-memory example data.
  }
}

export function record(kind, source, message) {
  demo.events.push([new Date().toISOString(), kind, source, message]);
  saveDemo();
}
