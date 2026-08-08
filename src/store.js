'use strict';

/**
 * In-memory store for automation rules and their run history.
 *
 * The store is intentionally dependency-free so the app can run end-to-end in
 * any environment without provisioning a database. Swap this out for a real
 * persistence layer when one is needed.
 */

let nextId = 1;
let rules = [];
let runLog = [];

const VALID_TRIGGERS = ['manual', 'schedule', 'webhook'];

function reset() {
  nextId = 1;
  rules = [];
  runLog = [];
}

function seed() {
  reset();
  createRule({
    name: 'Günlük rapor',
    trigger: 'schedule',
    action: 'Send daily summary email',
  });
  createRule({
    name: 'Webhook bildirimi',
    trigger: 'webhook',
    action: 'Post message to Slack',
  });
}

function listRules() {
  return rules.map((r) => ({ ...r }));
}

function getRule(id) {
  const rule = rules.find((r) => r.id === id);
  return rule ? { ...rule } : null;
}

function createRule({ name, trigger, action }) {
  const cleanName = typeof name === 'string' ? name.trim() : '';
  const cleanAction = typeof action === 'string' ? action.trim() : '';
  const cleanTrigger = typeof trigger === 'string' ? trigger.trim() : '';

  if (!cleanName) {
    const err = new Error('Rule name is required');
    err.status = 400;
    throw err;
  }
  if (!cleanAction) {
    const err = new Error('Rule action is required');
    err.status = 400;
    throw err;
  }
  if (!VALID_TRIGGERS.includes(cleanTrigger)) {
    const err = new Error(
      `Trigger must be one of: ${VALID_TRIGGERS.join(', ')}`
    );
    err.status = 400;
    throw err;
  }

  const rule = {
    id: nextId++,
    name: cleanName,
    trigger: cleanTrigger,
    action: cleanAction,
    enabled: true,
    runCount: 0,
    createdAt: new Date().toISOString(),
  };
  rules.push(rule);
  return { ...rule };
}

function setEnabled(id, enabled) {
  const rule = rules.find((r) => r.id === id);
  if (!rule) return null;
  rule.enabled = Boolean(enabled);
  return { ...rule };
}

function deleteRule(id) {
  const idx = rules.findIndex((r) => r.id === id);
  if (idx === -1) return false;
  rules.splice(idx, 1);
  return true;
}

/**
 * "Runs" a rule. A disabled rule cannot be run. Each run increments the rule's
 * run counter and appends an entry to the run log.
 */
function runRule(id) {
  const rule = rules.find((r) => r.id === id);
  if (!rule) return null;
  if (!rule.enabled) {
    const err = new Error('Cannot run a disabled rule');
    err.status = 409;
    throw err;
  }
  rule.runCount += 1;
  const entry = {
    id: runLog.length + 1,
    ruleId: rule.id,
    ruleName: rule.name,
    action: rule.action,
    status: 'success',
    ranAt: new Date().toISOString(),
  };
  runLog.unshift(entry);
  return { rule: { ...rule }, entry: { ...entry } };
}

function listRuns() {
  return runLog.map((r) => ({ ...r }));
}

module.exports = {
  VALID_TRIGGERS,
  reset,
  seed,
  listRules,
  getRule,
  createRule,
  setEnabled,
  deleteRule,
  runRule,
  listRuns,
};
