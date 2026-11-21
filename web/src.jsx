import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';

async function api(path, data) {
  const options = data && {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)};
  const response = await fetch('/api/' + path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error);
  return body;
}

const marks = {metric: '∑', join: '↔', entity: '◇', property: '·'};
const number = new Intl.NumberFormat();
const status = change => change.decision === 'approve' ? 'Approved' :
  change.decision === 'reject' ? 'Rejected' : change.validation.ok ? 'Pending' : 'Blocked';

function ObjectMark({kind}) {
  return <span className={'object-mark ' + kind} aria-hidden="true">{marks[kind] ?? '◇'}</span>;
}

function Json({value}) {
  return <pre>{JSON.stringify(value, null, 2)}</pre>;
}

function describe(obj) {
  const table = obj?.table;
  const joins = obj?.kind === 'join'
    ? [table + '.' + obj.left + ' → ' + obj.target + '.' + obj.right]
    : obj?.joins?.map(j => table + '.' + j.left + ' → ' + j.table + '.' + j.right);
  return [
    ['Grain', obj?.key && 'One row per ' + table + '.' + obj.key],
    ['Join path', joins?.join(' · ') || (obj?.kind === 'metric' ? 'Base table only' : null)],
    ['Metric', obj?.op && obj.op.toUpperCase() + '(' + (obj.column || '*') + ')'
      + (obj.where ? ' where ' + obj.where.column + ' = ' + obj.where.value : '')],
    ['Property', obj?.column && obj.kind === 'property' ? table + '.' + obj.column : null],
    ['Role visibility', Array.isArray(obj?.roles) ? obj.roles.join(', ') : null],
  ];
}

function Definition({before, after}) {
  const previous = Object.fromEntries(describe(before));
  const proposed = describe(after).filter(([name, value]) => value != null || previous[name] != null);
  return <>
    <table className="definition-table"><thead><tr><th scope="col">Field</th><th scope="col">Before</th><th scope="col">Proposed</th></tr></thead>
      <tbody>{proposed.map(([name, value]) => <tr key={name} className={value !== previous[name] ? 'changed' : ''}>
        <th scope="row">{name}</th><td>{previous[name] ?? '—'}</td><td>{value ?? '—'}</td>
      </tr>)}</tbody></table>
    <details><summary>Raw definitions</summary>
      {before && <><h3>Previous definition</h3><Json value={before}/></>}
      <h3>Proposed definition</h3><Json value={after}/>
    </details>
  </>;
}

function queueGroups(changes) {
  const groups = new Map();
  for (const change of changes) {
    const table = change.after.table;
    const blocked = status(change) === 'Blocked';
    const key = (blocked ? 'blocked:' : 'table:') + table;
    if (!groups.has(key)) groups.set(key, {key, label: blocked ? 'Needs attention / ' + table : table, changes: []});
    groups.get(key).changes.push(change);
  }
  return [...groups.values()];
}

function Inspector({change, onDecide, busy, published}) {
  const {after, before, validation, decision} = change;
  const {evidence, ...definition} = after;
  const [tab, setTab] = useState('Validation');
  const fanout = validation.rows > validation.distinct_keys;
  const compareTotals = fanout && validation.base_value != null && validation.value != null;
  const confidence = Number.isFinite(after.confidence) ? Math.round(after.confidence * 100) + '%' : '—';
  const counts = validation.expected_rows !== undefined ? [
    ['Expected rows', validation.expected_rows],
    ['Joined rows', validation.rows],
    ['Distinct keys', validation.distinct_keys],
  ] : [];
  return <section className="inspector" aria-label="Change details">
    <div className="inspector-head">
      <div className="object-line"><ObjectMark kind={after.kind}/><div className="object-type">{before ? 'Update' : 'Add'} {after.kind}</div></div>
      <h2>{after.name}</h2>
      {after.description && <p className="description">{after.description}</p>}
      <dl className="metadata">
        <div><dt>Source table</dt><dd>{after.table}</dd></div>
        <div><dt>Confidence</dt><dd>{confidence}</dd></div>
        <div><dt>Visible to</dt><dd>{Array.isArray(after.roles) ? after.roles.join(', ') : '—'}</dd></div>
      </dl>
    </div>
    <nav className="tabs" aria-label="Inspector views">
      {['Validation', 'Definition', 'Evidence'].map(name => <button key={name} aria-pressed={tab === name}
        onClick={() => setTab(name)}>{name}{name === 'Evidence' && <span>{evidence?.length ?? 0}</span>}</button>)}
    </nav>
    <div className="inspector-body">
      {tab === 'Validation' && <>
        <div className={'callout ' + (validation.ok ? 'pass' : 'fail')}>
          <strong>{fanout ? number.format(validation.rows) + ' joined rows for ' +
            number.format(validation.distinct_keys) + ' ' + after.table :
            validation.ok ? 'Validation passed' : 'Validation failed'}</strong>
          {validation.reason && <p>{validation.reason}</p>}
          {!validation.reason && validation.rows > validation.distinct_keys && <p>The join duplicates base keys and can inflate the metric.</p>}
          {!validation.reason && !validation.ok && validation.rows === validation.distinct_keys && <p>Review the measured row counts, null rate, and result.</p>}
        </div>
        {compareTotals && <dl className="total-comparison">
          <div><dt>Base-grain total</dt><dd>{number.format(validation.base_value)}</dd></div>
          <div><dt>Proposed total</dt><dd>{number.format(validation.value)}</dd></div>
        </dl>}
        {counts.length > 0 && <dl className="counts">{counts.map(([label, value]) => <div key={label}>
          <dt>{label}</dt><dd>{number.format(value)}</dd></div>)}</dl>}
        {validation.sql && <><h3>Compiled SQL</h3><pre>{validation.sql}</pre></>}
        <details><summary>Validation output</summary><Json value={validation}/></details>
      </>}
      {tab === 'Definition' && <Definition before={before} after={definition}/>}
      {tab === 'Evidence' && (evidence?.length ? evidence.map((entry, i) => <div className="evidence" key={i}>
        <h3>{entry.tool ?? 'Source observation'} · {i + 1}</h3>
        {(entry.sql || entry.args?.sql) && <pre>{entry.sql || entry.args.sql}</pre>}
        <details><summary>Full evidence record</summary><Json value={entry}/></details>
      </div>) : <p className="empty">No proposer evidence attached.</p>)}
    </div>
    <footer className="decision-bar">
      <span className={'badge ' + status(change).toLowerCase()}>{status(change)}</span>
      <div className="button-group">
        <button className="secondary" disabled={busy || published} aria-pressed={decision === 'reject'} aria-keyshortcuts="R" aria-label="Reject"
          onClick={() => onDecide(change.id, 'reject')}>Reject <kbd>R</kbd></button>
        <button className="primary" disabled={busy || published || !validation.ok} aria-pressed={decision === 'approve'} aria-keyshortcuts="A" aria-label="Approve"
          onClick={() => onDecide(change.id, 'approve')}>Approve <kbd>A</kbd></button>
      </div>
    </footer>
  </section>;
}

function ChangeGate({batch, ready, busy, onCheck, onAcknowledge}) {
  const report = batch.gate;
  return <section className="change-gate" aria-label="Change gate">
    <div className="gate-head">
      <div><h2>Change gate</h2>
        <p>{report ? (report.blocked ? 'Publication blocked' :
          report.findings.length ? (report.acknowledged ? 'Answer changes acknowledged' : 'Answer changes require acknowledgement') :
          'No answer or access changes detected') : 'Replay saved answers against this proposal.'}</p>
      </div>
      {!batch.published && <button className="secondary" disabled={busy || !ready} onClick={onCheck}>Run gate</button>}
    </div>
    {report && <>
      <p className="gate-meta">{report.checked} of {report.cases} plans replayed · {report.mode === 'fallback' ? 'Full replay due to incomplete dependencies' : report.mode}</p>
      {report.failed_invariants.length > 0 && <p className="gate-meta">Failed validation: {report.failed_invariants.map(x => x.name).join(', ')}</p>}
      {report.findings.length > 0 && <table className="gate-table">
        <thead><tr><th scope="col">Saved plan</th><th scope="col">Role</th><th scope="col">Current</th><th scope="col">Proposed</th><th scope="col">Finding</th></tr></thead>
        <tbody>{report.findings.map(row => <tr key={row.id}>
          <th scope="row">{row.id}</th><td>{row.role}</td>
          <td>{typeof row.before === 'number' ? number.format(row.before) : row.before ?? '—'}</td>
          <td>{typeof row.after === 'number' ? number.format(row.after) : row.after ?? '—'}</td>
          <td>{row.status === 'answer_changed' ? 'Answer changed' : row.status === 'access_changed' ? 'Access changed' : 'Execution error'}</td>
        </tr>)}</tbody>
      </table>}
      {!batch.published && !report.blocked && !report.acknowledged && report.findings.length > 0 &&
        <button className="secondary gate-ack" disabled={busy} onClick={() => onAcknowledge(report.evidence_key)}>Acknowledge answer changes</button>}
    </>}
  </section>;
}

function Review() {
  const [input, setInput] = useState(new URLSearchParams(location.search).get('batch') || '1');
  const [batch, setBatch] = useState(null);
  const [selected, setSelected] = useState(null);
  const [filter, setFilter] = useState('All');
  const [search, setSearch] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(true);

  async function load(id) {
    const data = await api('review/' + encodeURIComponent(id));
    setBatch(data);
    return data;
  }

  useEffect(() => {
    load(input).catch(e => setError(e.message)).finally(() => setBusy(false));
  }, []);

  async function act(fn) {
    setBusy(true);
    setError('');
    try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  const changes = batch?.changes ?? [];
  const decided = changes.filter(c => c.decision).length;
  const approved = changes.filter(c => c.decision === 'approve').length;
  const blocked = changes.filter(c => !c.validation.ok && !c.decision).length;
  const matches = changes.filter(c => (filter === 'All' || status(c) === filter) &&
    (c.after.name + ' ' + c.after.kind + ' ' + c.after.table).toLowerCase().includes(search.trim().toLowerCase()));
  const blockedFirst = [...matches.filter(c => status(c) === 'Blocked'), ...matches.filter(c => status(c) !== 'Blocked')];
  const groups = queueGroups(blockedFirst);
  const hits = groups.flatMap(group => group.changes);
  const current = hits.find(c => c.id === selected) ?? hits[0];
  const published = batch?.published != null;
  const ready = approved > 0 && decided === changes.length;
  const needsGate = batch?.parent > 0;
  const gate = batch?.gate;
  const gateReady = !needsGate || (gate && !gate.blocked && (!gate.findings.length || gate.acknowledged));

  function decide(id, decision) {
    setSelected(id);
    return act(async () => {await api('decide', {id, decision}); await load(batch.id);});
  }

  useEffect(() => {
    function shortcut(event) {
      if (!current || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey ||
          event.repeat || event.target.closest?.('input, select, textarea, [contenteditable]')) return;
      const key = event.key.toLowerCase();
      if (key === 'j' || key === 'k') {
        event.preventDefault();
        const index = hits.indexOf(current) + (key === 'j' ? 1 : -1);
        const next = hits[index];
        if (next) {
          setSelected(next.id);
          document.getElementById('change-' + next.id)?.focus({preventScroll: true});
          document.getElementById('change-' + next.id)?.scrollIntoView({block: 'nearest'});
        }
      } else if (!busy && !published && (key === 'r' || (key === 'a' && current.validation.ok))) {
        event.preventDefault();
        decide(current.id, key === 'a' ? 'approve' : 'reject');
      }
    }
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  });


  return <>
    <header className="masthead">
      <div className="brand"><span className="brand-mark" aria-hidden="true">◎</span> Ontology Proposer</div>
      <form className="batch-form" onSubmit={e => {e.preventDefault(); act(async () => {
        const data = await load(input);
        setFilter('All'); setSearch(''); setSelected(null);
        history.replaceState(null, '', '?batch=' + data.id);
      });}}>
        <label htmlFor="batch">Batch</label>
        <input id="batch" type="number" min="1" required value={input} onChange={e => setInput(e.target.value)}/>
        <button className="secondary" disabled={busy}>Load</button>
      </form>
    </header>
    <main>
      <section className="hero">
        <div><p className="context">{batch ? 'Batch ' + batch.id + ' / Base version ' + batch.parent : 'Ontology versions'}</p>
          <h1>Proposal review</h1></div>
        <button className="primary publish" disabled={busy || published || !ready || !gateReady}
          onClick={() => act(async () => {await api('publish', {batch: batch.id}); await load(batch.id);})}>
          {published ? 'Version ' + batch.published + ' published' : 'Publish version'}
        </button>
      </section>
      {error && <p role="alert" className="callout fail">{error}</p>}
      {batch && <section className="overview" aria-label="Review progress">
        <div className="overview-progress"><div><strong>{decided} of {changes.length}</strong> reviewed</div>
          <progress value={decided} max={changes.length || 1} aria-label="Changes reviewed"/></div>
        <div><strong>{approved}</strong><span> Approved</span></div>
        <div><strong>{decided - approved}</strong><span> Rejected</span></div>
        <div><strong>{blocked}</strong><span> Blocked</span></div>
      </section>}
      {needsGate && <ChangeGate batch={batch} ready={ready} busy={busy}
        onCheck={() => act(async () => {await api('gate', {batch: batch.id}); await load(batch.id);})}
        onAcknowledge={key => act(async () => {await api('gate/acknowledge', {batch: batch.id, key}); await load(batch.id);})}/>}
      <div className="workspace">
        <section className="queue" aria-label="Change queue">
          <div className="queue-head">
            <h2>Changes <span>{hits.length}</span></h2>
            <input aria-label="Search changes" type="search" placeholder="Search changes…" value={search} onChange={e => setSearch(e.target.value)}/>
            <label className="filter">Status<select value={filter} onChange={e => setFilter(e.target.value)}>
              {['All', 'Pending', 'Blocked', 'Approved', 'Rejected'].map(s => <option key={s}>{s}</option>)}
            </select></label>
          </div>
          <div className="queue-scroll">
            <table className="change-table">
              <thead><tr><th scope="col">Change</th><th scope="col">Decision</th></tr></thead>
              {groups.map(group => <tbody key={group.key}>
                <tr className="group-heading"><th scope="rowgroup" colSpan="2">{group.label}</th></tr>
                {group.changes.map(c => <tr key={c.id} className={c.id === current?.id ? 'active' : ''}>
                  <td><button id={'change-' + c.id} className="queue-row" aria-current={c.id === current?.id}
                    onClick={e => {setSelected(c.id); if (e.detail) e.currentTarget.blur();}}>
                    <ObjectMark kind={c.after.kind}/><span className="row-info"><strong>{c.after.name}</strong><small>{c.after.kind}</small></span>
                  </button></td>
                  <td><span className={'badge ' + status(c).toLowerCase()}>{status(c)}</span></td>
                </tr>)}
              </tbody>)}
            </table>
          </div>
          {hits.length > 0 && <div className="queue-shortcuts"><kbd>J</kbd> next <kbd>K</kbd> previous</div>}
          {!hits.length && <p className="empty">{busy && !batch ? 'Loading changes…' : 'No matching changes.'}</p>}
        </section>
        {current ? <Inspector key={current.id} change={current} busy={busy} published={published}
          onDecide={decide}/>
          : <div className="inspector empty">Select a change to inspect its evidence.</div>}
      </div>
    </main>
  </>;
}

createRoot(document.getElementById('root')).render(<Review/>);
