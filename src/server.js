'use strict';

const { createApp } = require('./app');
const store = require('./store');

const PORT = process.env.PORT || 3000;
const HOST = process.env.HOST || '0.0.0.0';

// Seed a couple of example rules so the dashboard is not empty on first boot.
store.seed();

const app = createApp();

app.listen(PORT, HOST, () => {
  // eslint-disable-next-line no-console
  console.log(`Otomasyon dashboard running at http://${HOST}:${PORT}`);
});
