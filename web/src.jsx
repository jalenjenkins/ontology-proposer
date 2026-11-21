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

function Inspector({change, onDecide, busy, published}) {
  const {after, before, validation, decision} = change;
  const {evidence, ...definition} = after;
  const [tab, setTab] = useState('Validation');
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
          <strong>{validation.ok ? 'Validation passed' : 'Validation failed'}</strong>
          {validation.reason && <p>{validation.reason}</p>}
          {!validation.reason && validation.rows > validation.distinct_keys && <p>The join duplicates base keys and can inflate the metric.</p>}
          {!validation.reason && !validation.ok && validation.rows === validation.distinct_keys && <p>Review the measured row counts, null rate, and result.</p>}
        </div>
        {counts.length > 0 && <dl className="counts">{counts.map(([label, value]) => <div key={label}>
          <dt>{label}</dt><dd>{number.format(value)}</dd></div>)}</dl>}
        {validation.sql && <><h3>Compiled SQL</h3><pre>{validation.sql}</pre></>}
        <details><summary>Validation output</summary><Json value={validation}/></details>
      </>}
      {tab === 'Definition' && <>
        {before && <><h3>Previous definition</h3><Json value={before}/></>}
        <h3>Proposed definition</h3><Json value={definition}/>
      </>}
      {tab === 'Evidence' && (evidence?.length ? evidence.map((entry, i) => <div className="evidence" key={i}>
        <h3>{entry.tool ?? 'Source observation'} · {i + 1}</h3>
        {(entry.sql || entry.args?.sql) && <pre>{entry.sql || entry.args.sql}</pre>}
        <details><summary>Full evidence record</summary><Json value={entry}/></details>
      </div>) : <p className="empty">No proposer evidence attached.</p>)}
    </div>
    <footer className="decision-bar">
      <span className={'badge ' + status(change).toLowerCase()}>{status(change)}</span>
      <div className="button-group">
        <button className="secondary" disabled={busy || published} aria-pressed={decision === 'reject'}
          onClick={() => onDecide(change.id, 'reject')}>Reject</button>
        <button className="primary" disabled={busy || published || !validation.ok} aria-pressed={decision === 'approve'}
          onClick={() => onDecide(change.id, 'approve')}>Approve</button>
      </div>
    </footer>
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
  const hits = changes.filter(c => (filter === 'All' || status(c) === filter) &&
    (c.after.name + ' ' + c.after.kind + ' ' + c.after.table).toLowerCase().includes(search.trim().toLowerCase()));
  const current = hits.find(c => c.id === selected) ?? hits[0];
  const published = batch?.published != null;

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
        <button className="primary publish" disabled={busy || published || !approved || decided !== changes.length}
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
      <div className="workspace">
        <section className="queue" aria-label="Change queue">
          <div className="queue-head">
            <h2>Changes <span>{hits.length}</span></h2>
            <input aria-label="Search changes" type="search" placeholder="Search changes…" value={search} onChange={e => setSearch(e.target.value)}/>
            <label className="filter">Status<select value={filter} onChange={e => setFilter(e.target.value)}>
              {['All', 'Pending', 'Blocked', 'Approved', 'Rejected'].map(s => <option key={s}>{s}</option>)}
            </select></label>
          </div>
          <ul>{hits.map(c => <li key={c.id}><button className="queue-row" aria-current={c.id === current?.id}
            onClick={() => setSelected(c.id)}>
            <ObjectMark kind={c.after.kind}/><span className="row-info"><strong>{c.after.name}</strong><small>{c.after.kind}</small></span>
            <span className={'badge ' + status(c).toLowerCase()}>{status(c)}</span>
          </button></li>)}</ul>
          {!hits.length && <p className="empty">{busy && !batch ? 'Loading changes…' : 'No matching changes.'}</p>}
        </section>
        {current ? <Inspector key={current.id} change={current} busy={busy} published={published}
          onDecide={(id, decision) => act(async () => {await api('decide', {id, decision}); await load(batch.id);})}/>
          : <div className="inspector empty">Select a change to inspect its evidence.</div>}
      </div>
    </main>
  </>;
}

createRoot(document.getElementById('root')).render(<Review/>);
