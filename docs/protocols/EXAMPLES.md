# Exemplo de troca ACP

Execute `uv run python -m aec.simulation.agents` para criar um SQLite temporário e ver a contagem de mensagens. Passe um caminho para conservar o banco: `uv run python -m aec.simulation.agents demo.sqlite`. O controlador cria objetos Python para orquestrador, implementador, QA e quality gate. Ele envia tarefa, recebe artefato, pede revisão, recebe finding, solicita correção, recebe segunda versão, reavalia, registra validação positiva e conclui. O broker apenas transporta as mensagens; o controlador escolhe a sequência.

Um envelope inicial tem esta forma:

```json
{
  "protocol": "ACP",
  "version": "0.1",
  "message_id": "task-message-001",
  "run_id": "demo-run",
  "task_id": "demo-task",
  "sender": "orchestrator",
  "recipient": "implementer",
  "type": "TASK_ASSIGNED",
  "iteration": 0,
  "created_at": "2026-10-17T12:00:00Z",
  "payload": {
    "title": "Implementar exemplo",
    "instructions": "Produzir artefato simulado"
  }
}
```

Para usar a API, crie `Broker(path)`, registre cada agente com `register(agent_id, Role)`, construa `Message.model_validate(envelope)`, chame `publish(message, identity)` e receba com `claim(agent_id, identity)`. Depois do processamento, confirme com `ack(claim, identity)` ou falhe com `nack(claim, reason, identity)`. Consulte `get_history(run_id)` para inspecionar a troca persistida.
