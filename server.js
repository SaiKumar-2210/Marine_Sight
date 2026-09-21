const { createApp } = require('./server/app');

createApp()
  .then(ctx => {
    ctx.start();
    const shutdown = () => ctx.stop().finally(() => process.exit(0));
    process.on('SIGINT', shutdown);
    process.on('SIGTERM', shutdown);
  })
  .catch(err => {
    console.error('Failed to start MarineSight API:', err);
    process.exit(1);
  });
