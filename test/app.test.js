'use strict';

const request = require('supertest');
const { createApp } = require('../src/app');
const store = require('../src/store');

let app;

beforeEach(() => {
  store.reset();
  app = createApp();
});

describe('health', () => {
  it('reports ok', async () => {
    const res = await request(app).get('/api/health');
    expect(res.status).toBe(200);
    expect(res.body.status).toBe('ok');
  });
});

describe('rules CRUD', () => {
  it('starts empty', async () => {
    const res = await request(app).get('/api/rules');
    expect(res.status).toBe(200);
    expect(res.body).toEqual([]);
  });

  it('creates a rule', async () => {
    const res = await request(app)
      .post('/api/rules')
      .send({ name: 'Backup', trigger: 'schedule', action: 'Run backup' });
    expect(res.status).toBe(201);
    expect(res.body).toMatchObject({
      name: 'Backup',
      trigger: 'schedule',
      action: 'Run backup',
      enabled: true,
      runCount: 0,
    });
    expect(res.body.id).toBeGreaterThan(0);
  });

  it('rejects a rule with a missing name', async () => {
    const res = await request(app)
      .post('/api/rules')
      .send({ trigger: 'manual', action: 'Do thing' });
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/name is required/i);
  });

  it('rejects an invalid trigger', async () => {
    const res = await request(app)
      .post('/api/rules')
      .send({ name: 'X', trigger: 'bogus', action: 'Y' });
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/trigger must be one of/i);
  });

  it('deletes a rule', async () => {
    const created = await request(app)
      .post('/api/rules')
      .send({ name: 'Temp', trigger: 'manual', action: 'nothing' });
    const del = await request(app).delete(`/api/rules/${created.body.id}`);
    expect(del.status).toBe(204);
    const list = await request(app).get('/api/rules');
    expect(list.body).toEqual([]);
  });
});

describe('running rules', () => {
  it('runs an enabled rule and records the run', async () => {
    const created = await request(app)
      .post('/api/rules')
      .send({ name: 'Notify', trigger: 'webhook', action: 'ping slack' });
    const id = created.body.id;

    const run = await request(app).post(`/api/rules/${id}/run`);
    expect(run.status).toBe(200);
    expect(run.body.rule.runCount).toBe(1);
    expect(run.body.entry.status).toBe('success');

    const runs = await request(app).get('/api/runs');
    expect(runs.body).toHaveLength(1);
    expect(runs.body[0].ruleName).toBe('Notify');
  });

  it('refuses to run a disabled rule', async () => {
    const created = await request(app)
      .post('/api/rules')
      .send({ name: 'Off', trigger: 'manual', action: 'x' });
    const id = created.body.id;

    await request(app).patch(`/api/rules/${id}`).send({ enabled: false });
    const run = await request(app).post(`/api/rules/${id}/run`);
    expect(run.status).toBe(409);
    expect(run.body.error).toMatch(/disabled/i);
  });

  it('returns 404 when running a missing rule', async () => {
    const run = await request(app).post('/api/rules/999/run');
    expect(run.status).toBe(404);
  });
});
