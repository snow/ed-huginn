# Development Notes

## Environment Quirks

### Make command

On this system, `make` conflicts with a zsh autoload function. Use the full path:

```bash
/usr/bin/make prepare
/usr/bin/make run
```

Or create an alias in your shell config:

```bash
alias make=/usr/bin/make
```
