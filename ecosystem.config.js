// pm2 process definition for the daily publisher.
//
// This is a *oneshot* job, not a service, which pm2 needs to be told explicitly:
//
//   autorestart: false   pm2's default is to restart anything that exits. Without this the
//                        script would run in a tight loop forever the moment it finished.
//   cron_restart         pm2 starts the process again on this schedule. Between runs the
//                        process shows as `stopped` in `pm2 ls` — that is the correct and
//                        expected state here, not a fault.
//
// No secrets live in this file. It is committed, and `pm2 save` also writes the captured
// environment into ~/.pm2/dump.pm2 — so a token here would end up in two places that are easy
// to forget. The script reads /etc/wshistory.env itself at runtime instead.
module.exports = {
  apps: [
    {
      name: 'wshistory',
      script: 'wshistory.py',
      interpreter: 'python3',
      args: 'publish',
      cwd: '/opt/wshistory',

      autorestart: false,
      // Hourly at :07. Upstream publishes a day around 03:00 UTC; running hourly means the
      // exact time never matters and a missed hour costs nothing, because `publish` is
      // idempotent and fills any gap in its window.
      cron_restart: '7 * * * *',

      out_file: '/var/log/wshistory/out.log',
      error_file: '/var/log/wshistory/err.log',
      merge_logs: true,
      time: true,
    },
  ],
};
