# alan-claude-agents

Marketplace personal de Claude Code con el plugin `dev-agents`: 13 agentes especialistas (más `sap-automation`, que es de proyecto).

## Instalar en una PC nueva

```bash
claude plugin marketplace add <tu-usuario>/alan-claude-agents
claude plugin install dev-agents@alan-agents
```

Listo. Verificá con `/agents` dentro de una sesión.

## Actualizar

Editá los agentes en `plugins/dev-agents/agents/`, subí `version` en `plugins/dev-agents/.claude-plugin/plugin.json` (por ejemplo, 1.0.0 → 1.1.0), commiteá y pusheá. En cada PC:

```bash
claude plugin marketplace update alan-agents
claude plugin update dev-agents@alan-agents
```

## Usar como sesión principal

```bash
claude --agent orquestador
claude --agent sparring
```

Si no encuentra el agente por nombre, probá con el prefijo del plugin (`claude --agent dev-agents:sparring`) o mirá el nombre exacto con `/agents`.

## Agente de proyecto (no va en el plugin)

`proyecto-arbolado/sap-automation.md` es específico del repo de arbolado. Copialo en ese repo y commitealo:

```bash
mkdir -p /ruta/repo-arbolado/.claude/agents
cp proyecto-arbolado/sap-automation.md /ruta/repo-arbolado/.claude/agents/
```

## Probar cambios sin pushear

```bash
claude --plugin-dir ./plugins/dev-agents
```
