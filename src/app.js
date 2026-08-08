'use strict';

const path = require('path');
const express = require('express');
const store = require('./store');

function createApp() {
  const app = express();
  app.use(express.json());

  const publicDir = path.join(__dirname, '..', 'public');
  app.use(express.static(publicDir));

  app.get('/api/health', (req, res) => {
    res.json({ status: 'ok', uptime: process.uptime() });
  });

  app.get('/api/rules', (req, res) => {
    res.json(store.listRules());
  });

  app.post('/api/rules', (req, res, next) => {
    try {
      const rule = store.createRule(req.body || {});
      res.status(201).json(rule);
    } catch (err) {
      next(err);
    }
  });

  app.patch('/api/rules/:id', (req, res) => {
    const id = Number(req.params.id);
    const updated = store.setEnabled(id, req.body && req.body.enabled);
    if (!updated) return res.status(404).json({ error: 'Rule not found' });
    res.json(updated);
  });

  app.delete('/api/rules/:id', (req, res) => {
    const id = Number(req.params.id);
    const ok = store.deleteRule(id);
    if (!ok) return res.status(404).json({ error: 'Rule not found' });
    res.status(204).end();
  });

  app.post('/api/rules/:id/run', (req, res, next) => {
    try {
      const id = Number(req.params.id);
      const result = store.runRule(id);
      if (!result) return res.status(404).json({ error: 'Rule not found' });
      res.json(result);
    } catch (err) {
      next(err);
    }
  });

  app.get('/api/runs', (req, res) => {
    res.json(store.listRuns());
  });

  // eslint-disable-next-line no-unused-vars
  app.use((err, req, res, next) => {
    const status = err.status || 500;
    res.status(status).json({ error: err.message || 'Internal Server Error' });
  });

  return app;
}

module.exports = { createApp };
