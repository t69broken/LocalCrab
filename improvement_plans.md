# LocalCrab Improvement Plans
## Based on Autoresearch Principles

---

## Executive Summary

LocalCrab is experiencing systematic failures in agent execution, memory operations, and watchdog monitoring (25% success rate). The system has functional API endpoints (port 18798) and GPU capacity (4.93GB free / 20GB total VRAM). This document outlines actionable improvement items across 7 priority areas.

**Date:** Saturday, April 18, 2026  
**Current Cycle:** 2  
**Status:** Active  

---

## 1. AGENT EXECUTION RELIABILITY

### Current State
- Tests return "NO RESULT" consistently
- `http://localhost:18798/api/execute` endpoint may not be returning responses
- Timeout errors on agent task execution (0.1-0.3s failures)
- Missing actual task response handling

### Improvement Plan

#### 1.1 Enhanced Error Handling in API Calls
**Approach:** Add comprehensive exception handling with fallback strategies

```python
# In task_watchdog.py and API endpoints
async def execute_task_with_retry(task, prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            result = await self.agent_manager.execute(task, prompt)
            if self._parse_result(result):
                return result
        except Exception as e:
            log.error(f"Attempt {attempt+1} failed: {e}")
            if attempt >= max_retries:
                raise
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
```

#### 1.2 Task Execution Timeout Monitoring
**Approach:** Implement real-time timeout detection and graceful degradation

**Priority:** HIGH  
**Implementation:** Replace existing timeout handling with configurable limits

---

## 2. MEMORY SYSTEM STABILITY

### Current State
- Memory API endpoints failing (`/api/memory/save` and `/api/memory/search`)
- Possible SQLite database connectivity issues
- No retry logic for transient failures
- Missing memory validation before save

### Improvement Plan

#### 2.1 SQLite Connection Pooling
**Approach:** Implement connection pooling for reliability

```python
# Create connection pool in main.py
import sqlite3
from contextlib import contextmanager

@contextmanager
def get_db_connection():
    conn = sqlite3.connect(MEMORY_DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()
```

#### 2.2 Memory Validation Layer
**Approach:** Add pre-save validation and conflict resolution

```python
def validate_memory_entry(entry):
    if not entry.get('content'):
        raise ValueError("Memory content cannot be empty")
    
    # Check for duplicates
    existing = memory_search(entry.get('content')[:50])
    if existing and existing['importance'] > 0.5:
        return False  # Skip duplicate
    return True
```

**Priority:** HIGH  
**Implementation:** Wrap all memory operations in try-catch with logging

#### 2.3 Memory Backup Strategy
**Approach:** Periodic SQLite snapshots and WAL checkpointing

**Priority:** MEDIUM  
**Implementation:** Add cron job for `VACUUM` and backup to external storage

---

## 3. PERFORMANCE BENCHMARKING

### Current State
- No response time metrics collected
- Task completion rates unknown
- GPU utilization not tracked after task execution
- Memory usage not monitored during long tasks

### Improvement Plan

#### 3.1 Self-Benchmarking Metrics
**Area:** Performance Tracking  
**Current:** None  
**Improvement:** Implement metrics collection

**Metrics to Track:**

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| API Response Time | < 100ms | 0ms (no result) | ❌ FAIL |
| Task Completion Rate | > 80% | 25% | ❌ FAIL |
| GPU Memory Utilization | 0-75% VRAM | 25% free | ⚠️ WARN |
| Task Duration (median) | < 30s | Unknown | ❌ FAIL |
| Memory Ops Throughput | > 50 ops/min | 0 ops/min | ❌ FAIL |

**Implementation:** Add metrics endpoint to track all

```python
# New module: metrics_collector.py
class MetricsCollector:
    def __init__(self):
        self.responses = []
        self.errors = []
        self.gpu_samples = []
    
    def record_response(self, duration, success, size=0):
        self.responses.append({
            'ts': time.time(),
            'duration': duration,
            'success': success,
            'size': size
        })
    
    def get_percentiles(self, metric):
        values = [r['duration'] for r in self.responses 
                  if r['success'] and metric == 'response_time']
        if not values:
            return None
        import numpy as np
        return {
            'p50': np.percentile(values, 50),
            'p90': np.percentile(values, 90),
            'p99': np.percentile(values, 99),
            'avg': np.mean(values)
        }
```

**Priority:** HIGH  
**Implementation:** Add to `main.py` initialization

#### 3.2 Task Queue Limits
**Area:** Concurrency Control  
**Current:** No visible queue limits  
**Improvement:** Implement maximum concurrent tasks

**Implementation:**

```python
# In agent_manager.py
class TaskQueue:
    def __init__(self, max_concurrent=5):
        self.max_concurrent = max_concurrent
        self.active_tasks = 0
        self._lock = asyncio.Lock()
    
    async def submit(self, task_func, *args):
        async with self._lock:
            if self.active_tasks >= self.max_concurrent:
                # Queue task for later
                await asyncio.sleep(1)
        
        self.active_tasks += 1
        try:
            return await task_func(*args)
        finally:
            async with self._lock:
                self.active_tasks -= 1
```

**Priority:** MEDIUM  
**Implementation:** Add as utility module

#### 3.3 Performance Logging
**Approach:** Add structured logging with performance fields

```python
logger = logging.getLogger("localclaw")
logger.info(f"Task started: {task}")

start = time.time()
result = await execute_task(task)
duration = time.time() - start

logger.info(
    f"Task complete: {task} | "
    f"duration={duration:.2f}s | "
    f"steps={len(result.get('steps', []))} | "
    f"model={result.get('model')}"
)
```

**Priority:** HIGH  
**Implementation:** Modify existing logging calls

---

## 4. MULTI-AGENT COLLABORATION

### Current State
- 40 agents available, but agent execution failing
- No inter-agent communication protocols
- Task handoff between agents not implemented
- Self-planning agent exists but not integrated

### Improvement Plan

#### 4.1 Agent Task Routing
**Area:** Agent Orchestration  
**Current:** Single agent attempts all tasks  
**Improvement:** Route tasks to specialized agents

**Implementation:**

```python
# In agent_manager.py - add method
async def route_task(self, task: str) -> str:
    """Determine best agent for a task based on keywords"""
    task_lower = task.lower()
    
    routing_rules = {
        'search': 'search_agent',
        'browse': 'browser_agent', 
        'code': 'code_review_agent',
        'legal': 'legal_agent',
        'medical': 'medical_advisor',
        'plan': 'planner_agent',
        'execute': 'executor_agent',
        'default': 'default_agent'
    }
    
    for keyword, agent_type in routing_rules.items():
        if keyword in task_lower:
            return agent_type
    return 'default_agent'
```

#### 4.2 Task Decomposition with Self-Planning Agent
**Area:** Planning  
**Current:** Linear single-step execution  
**Improvement:** Break tasks into subtasks

**Implementation:** Use existing `self_planning_agent.py`

```python
# In agent_manager.py
async def execute_with_planning(self, task: str) -> AsyncGenerator:
    """Execute task with planning phase before action"""
    # Generate plan
    plan = await self.planning_agent.make_plan(task)
    
    # Execute plan steps
    async for step_result in self.execute_plan_steps(plan):
        yield step_result
    
    await self.planning_agent.evaluate(plan)
```

**Priority:** HIGH  
**Implementation:** Integrate into `main.py` agent loop

#### 4.3 Agent Health Monitoring
**Area:** Multi-Agent Coordination  
**Current:** No agent health tracking  
**Improvement:** Monitor agent availability and load

**Implementation:**

```python
# New module: agent_health_monitor.py
class AgentHealthMonitor:
    def __init__(self, agents):
        self.agents = agents
        self._health = {aid: 'healthy' for aid in agents}
        self._last_ping = {aid: time.time() for aid in agents}
        self._timeout = 300  # 5 minutes
    
    async def check_health(self):
        for agent_id in self.agents:
            agent = self.agents[agent_id]
            if time.time() - self._last_ping[agent_id] > self._timeout:
                self._health[agent_id] = 'timeout'
        
        await asyncio.sleep(self._heartbeat_interval)
        asyncio.create_task(self.check_health())
```

**Priority:** MEDIUM  
**Implementation:** Add monitoring background task

#### 4.4 Collaborative Task Execution
**Area:** Task Parallelization  
**Current:** Sequential single-agent execution  
**Improvement:** Parallelize independent subtasks

**Implementation:**

```python
async def execute_parallel_tasks(tasks: list[Task]):
    """Execute independent tasks in parallel"""
    semaphore = asyncio.Semaphore(3)  # Max 3 concurrent
    
    async def execute_with_limit(task):
        async with semaphore:
            await task.execute()
    
    await asyncio.gather(
        *[execute_with_limit(t) for t in tasks],
        return_exceptions=True
    )
```

**Priority:** HIGH  
**Implementation:** Add parallel execution option to API

#### 4.5 Inter-Agent Communication Protocol
**Area:** Agent Communication  
**Current:** No inter-agent messaging  
**Improvement:** Define event bus protocol

**Protocol Design:**

```python
# Events
AGENT_STARTED = "agent.started"
AGENT_STOPPED = "agent.stopped"  
TASK_ASSIGNED = "task.assigned"
TASK_COMPLETED = "task.completed"
TASK_FAILED = "task.failed"
HANDOFF_REQUEST = "agent.handoff"
HANDOFF_RESPONSE = "agent.handoff.response"

# Message format
{"type": "task.assigned", "task_id": "...", "from_agent": "..."}
{"type": "task.completed", "task_id": "...", "from_agent": "...", "result": "..."}
{"type": "handoff", "reason": "...", "target_agent": "...", "payload": "..."}
```

**Priority:** MEDIUM  
**Implementation:** Extend existing event bus in `task_watchdog.py`

---

## 5. ERROR RECOVERY PATTERNS

### Current State
- Failing tests return "NO RESULT" without specific errors  
- No automatic fallback between tools  
- No checkpoint system for long tasks  
- Memory operations fail silently

### Improvement Plan

#### 5.1 Tool Call Fallback
**Area:** Error Recovery  
**Current:** Tool fails, no retry with alternative  
**Improvement:** Try web_search when terminal fails

```python
@tool_call_decorator
def safe_call(tool, args):
    try:
        return tool(args)
    except TerminalUnavailableError:
        # Fallback for information tasks
        result = web_search(args)
        if result:
            return result
        raise
    except WebError as e:
        # Fallback to terminal for system queries
        return terminal(f"echo {args}")
```

**Priority:** HIGH  
**Implementation:** Add decorator to `hermes_format.py`

#### 5.2 Task Checkpoint System
**Area:** State Persistence  
**Current:** No checkpoints on long tasks  
**Improvement:** Save state every N steps

**Implementation:**

```python
# In task_watchdog.py
class CheckpointManager:
    def __init__(self, store):
        self.store = store
        self._interval = 10  # seconds
    
    async def save_checkpoint(self, job, state):
        """Save current task state for recovery"""
        checkpoint = {
            'job_id': job.job_id,
            'step': job.step,
            'context': self._serialize_context(job),
            'timestamp': time.time()
        }
        await self.store.save_checkpoint(checkpoint)
```

**Priority:** HIGH  
**Implementation:** Add to `task_recovery.py` integration

#### 5.3 Task Retry with Model Switch
**Area:** Resilience  
**Current:** Retry same model on failure  
**Improvement:** Switch models on repeated failures

```python
async def execute_with_model_fallback(task):
    models = ['llama3.2', 'llama3.1:8b', 'deepseek-r1:1.5b']
    
    for model in models:
        try:
            return await execute_task(task, model=model)
        except TimeoutError:
            if model == models[-1]:
                raise  # Last resort failed
            log.warning(f"Timeout on {model}, trying {models[models.index(model)+1]}")
        except Exception as e:
            if model == models[-1]:
                raise
            log.error(f"Error on {model}: {e}")
```

**Priority:** MEDIUM  
**Implementation:** Add to model selector in `main.py`

#### 5.4 Error Aggregation and Escalation
**Area:** Error Handling  
**Current:** Individual errors, no correlation  
**Improvement:** Group related errors, escalate patterns

**Implementation:**

```python
class ErrorAggregator:
    def __init__(self):
        self.errors_by_type = defaultdict(list)
        self._escalation_queue = Queue()
    
    def record_error(self, job_id, error):
        self.errors_by_type[error.type].append({
            'job_id': job_id,
            'error': error,
            'ts': time.time()
        })
        
        # Check if this indicates system-wide issue
        if len(self.errors_by_type[error.type]) > 10:
            await self._escalate_system_issue(error.type)
    
    async def _escalate_system_issue(self, issue_type):
        if issue_type == "memory":
            # Mark memory services as degraded
            pass
        elif issue_type == "network":
            # Switch to offline mode
            pass
```

**Priority:** MEDIUM  
**Implementation:** Add error aggregation to logging system

---

## 6. CONTEXT MANAGEMENT OPTIMIZATION

### Current State
- Long conversations not summarized
- Memory injection may be redundant
- No conversation state cleanup
- System prompt growth unbounded

### Improvement Plan

#### 6.1 Conversation Summarization
**Area:** Context Management  
**Current:** Full history retained  
**Improvement:** Summarize past N turns

**Implementation:**

```python
async def maintain_context(conversation, max_tokens=128000):
    """Maintain conversation within token limits"""
    
    def summarize_section(conversational_segment):
        """Create summary of segment"""
        model = "llama3.2"  # or smaller for summaries
        return await generate_summary(conversational_segment, model)
    
    if len(conversation) > CONTEXT_SIZE_THRESHOLD:
        # Summarize oldest segment
        summary = await summarize_section(
            conversation[:len(conversation)//10]
        )
        
        # Replace with summary
        conversation = [summary] + conversation[-(len(conversation)//10):]
    
    return conversation
```

**Priority:** HIGH  
**Implementation:** Add to agent context refresh in `main.py`

#### 6.2 Intelligent Memory Filtering
**Area:** Memory Management  
**Current:** All memories injected  
**Improvement:** Filter by relevance score

**Implementation:**

```python
async def get_relevant_memories(query: str, limit=8):
    """Get memories ranked by relevance to current task"""
    
    # Relevance search
    results = await memory_server.search(query)
    
    # Score each memory
    scored = []
    for mem in results:
        # Extract query terms
        query_terms = extract_terms(query)
        
        # Calculate relevance
        score = 0
        for term in query_terms:
            if term.lower() in mem['content'].lower():
                score += 1
            if term in mem.get('tags', []):
                score += 2
        
        scored.append((mem, min(score, 1.0)))
    
    # Filter low relevance
    return [(mem, score) for mem, score in scored[:limit] if score > 0.3]
```

**Priority:** MEDIUM  
**Implementation:** Modify memory injection in `agent_manager.py`

#### 6.3 System Prompt Caching
**Area:** Performance  
**Current:** System prompt rebuilt every time  
**Improvement:** Cache and incrementally update

**Implementation:**

```python
class PromptCache:
    def __init__(self, base_size=4000):
        self.cache = {}
        self.base_size = base_size
    
    async def get_cached_system_prompt(self, agent, query):
        """Return cached prompt or cache miss"""
        cache_key = f"{agent.name}:{hash(query)}"
        
        if cache_key in self.cache:
            # Check cache freshness (1 hour)
            if time.time() - self.cache[cache_key]['ts'] < 3600:
                self.cache[cache_key]['accesses'] += 1
                return self.cache[cache_key]['prompt']
        
        # Build fresh prompt
        prompt = await self._build_prompt(agent, query)
        
        # Cache it
        self.cache[cache_key] = {
            'prompt': prompt,
            'ts': time.time(),
            'accesses': 1
        }
        
        return prompt
    
    async def _build_prompt(self, agent, query):
        """Build prompt for current state"""
        # Inject dynamic components
        return CHAT_SYSTEM_TEMPLATE.format(...)
```

**Priority:** MEDIUM  
**Implementation:** Add to system initialization

#### 6.4 Context Window Monitoring
**Area:** Resource Management  
**Current:** Token usage unknown  
**Improvement:** Track and alert on window usage

**Implementation:**

```python
class ContextMonitor:
    def __init__(self, api):
        self.api = api
        self.window_size = 128000  # tokens
        self._current_usage = 0
    
    async def update_usage(self, agent, messages):
        """Update current token usage"""
        self._current_usage = estimate_tokens(messages)
        
        if self._current_usage > self.window_size * 0.9:
            log.warning(f"Context at {self._current_usage/self.window_size*100:.0f}%")
    
    async def summarize_and_compress(self, agent):
        """Compress context if needed"""
        if self._current_usage > self.window_size * 0.8:
            # Trigger summarization
            await maintain_context(agent.history)
            await self.update_usage(agent, agent.history)
```

**Priority:** HIGH  
**Implementation:** Add as asyncio background task

---

## 7. OTA UPDATE & VERSION CONTROL

### Current State
- OTA module exists but not integrated  
- No version tracking of agents
- No rollback mechanism  
- Skills directory not versioned

### Improvement Plan

#### 7.1 Versioned Agent Registry
**Area:** Deployment  
**Current:** Agent state unknown  
**Improvement:** Track agent versions with rollback

**Implementation:**

```python
class DeploymentTracker:
    def __init__(self, storage):
        self.storage = storage
        self.version_file = "agents_version.json"
    
    async def register_agent_version(self, agent_id, version, state):
        """Register agent as deployed at version"""
        entry = {
            'agent_id': agent_id,
            'version': version,
            'timestamp': time.time(),
            'state_hash': sha256(json.dumps(state).encode()),
            'parent_version': self.get_latest(agent_id)  # For rollback
        }
        
        await self.storage.write(
            path=f"agents/{agent_id}/versions/{version}",
            content=json.dumps(state, default=str)
        )
        
        return entry
    
    async def get_latest(self, agent_id):
        versions = await self.storage.list(path=f"agents/{agent_id}/versions")
        return max(versions, key=lambda v: parse_version(v))
```

**Priority:** HIGH  
**Implementation:** Add to `ota_upgrade.py` integration

#### 7.2 OTA Update Service
**Area:** Live Updates  
**Current:** No live updates  
**Improvement:** Safe hot-swappable components

**Implementation:**

```python
class OTAService:
    def __init__(self, config, storage):
        self.config = config
        self.storage = storage
        
        # List of updatable components
        self.components = [
            'agent_manager',
            'task_watchdog', 
            'memory_server',
            'model_selector'
        ]
    
    async def check_for_updates(self) -> list[dict]:
        """Check for available updates"""
        updates = []
        
        for component in self.components:
            # Check version against remote manifest
            remote_version = await fetch_latest_version(component)
            local_version = self.get_local_version(component)
            
            if parse_version(remote_version) > parse_version(local_version):
                updates.append({
                    'component': component,
                    'current': local_version,
                    'available': remote_version,
                    'changelog': await fetch_changelog(component, local_version, remote_version)
                })
        
        return updates
    
    async def apply_update(self, component, version):
        """Apply update with rollback capability"""
        
        # Backup current state
        backup_path = f"backups/{component}/{get_timestamp()}.backup"
        await self.storage.copy(
            src=f"agents/{component}",
            dst=backup_path
        )
        
        # Download new version
        new_path = f"agents/{component}/versions/{version}"
        await fetch_and_store(new_path)
        
        # Register deployment
        await self.storage.write(
            path=f"deployment_history/{now}",
            content=json.dumps({
                'component': component,
                'from': self.get_local_version(component),
                'to': version,
                'timestamp': time.time()
            })
        )
```

**Priority:** HIGH  
**Implementation:** Integrate into deployment workflow

#### 7.3 Skills Version Control
**Area:** Skill Management  
**Current:** Skills in-place no versioning  
**Improvement:** Version skills with compatibility tracking

**Implementation:**

```python
class SkillsRegistry:
    def __init__(self, skills_dir):
        self.skills_dir = skills_dir
        self._meta = {}
    
    async def install_skill(self, slug, repo, commit=None):
        """Install skill with version control"""
        
        # Check for existing
        existing = await self._meta.get(slug)
        if existing and not force:
            return {'status': 'exists', 'version': existing['version']}
        
        # Clone or pull
        if commit:
            await git_pull(f"{repo}/slug", commit)
        else:
            await git_pull(f"{repo}/slug")
        
        # Record installation
        entry = {
            'slug': slug,
            'repo': repo,
            'version': get_commit_hash(),
            'installed_at': time.time(),
            'capabilities': await detect_capabilities(slug)
        }
        
        self._meta[slug] = entry
    
    def get_skill(self, slug):
        """Get installed skill, falling back to cache"""
        if slug in self._meta:
            skill_path = self.skills_dir / slug
            return load_skill(skill_path)
        return None
```

**Priority:** MEDIUM  
**Implementation:** Modify existing skills_manager

---

## Implementation Order

### Phase 1: Critical Stability (Week 1)
1. **Agent Execution Reliability** (HIGH) - Fix failing tests
2. **Memory System Stability** (HIGH) - Add validation and retry
3. **Self-Benchmarking** (HIGH) - Add metrics collection
4. **Task Checkpoint System** (HIGH) - Enable recovery

### Phase 2: Performance & Efficiency (Week 2)
1. **Performance Logging** (HIGH) - Add timing to all operations
2. **Context Management** - Implement summarization
3. **Task Queue Limits** - Add concurrency control
4. **System Prompt Caching** - Reduce rebuild overhead

### Phase 3: Multi-Agent Enhancement (Week 3)
1. **Agent Task Routing** - Route to specialized agents
2. **Agent Health Monitoring** - Track availability
3. **Parallel Task Execution** - Enable concurrent work

### Phase 4: Deployment & Operations (Week 4)
1. **OTA Update System** - Versioned deployments
2. **Skills Versioning** - Version control for skills
3. **Error Aggregation** - Group and escalate errors

---

## Monitoring Dashboard Requirements

### Metrics to Expose
- Response times (p50, p90, p99)
- Task completion rate over time
- GPU utilization snapshots
- Memory operations throughput
- Agent health status
- Queue depths per task type
- Error types and frequencies

### Alerts
- Task completion rate < 50% for 10 minutes
- Memory operations failing > 5 times/minute
- GPU memory > 70% sustained
- Agent timeout events

---

## Test Plan Updates

### New Test Categories

#### 7.1 Performance Tests
- [ ] Response time under load (10 concurrent tasks)
- [ ] GPU memory growth pattern on long tasks
- [ ] Memory database connection pool exhaustion
- [ ] Context summarization correctness
- [ ] OTA update rollback success

#### 7.2 Memory Growth Tests
- [ ] 10K memories storage and retrieval
- [ ] Duplicate detection accuracy
- [ ] Memory relevance scoring
- [ ] SQLite checkpoint timing under load
- [ ] VACUUM operation timing

#### 7.3 Task Queue Limits
- [ ] Concurrency limit enforcement
- [ ] Queue depth under sustained load
- [ ] Task starvation prevention
- [ ] Parallel task coordination
- [ ] Deadlock detection and prevention

---

## Summary Table

| Area | Current State | Improvement Plan | Priority | Implementation |
|------|---------------|------------------|----------|----------------|
| **Agent Execution** | 25% success, no results | Retry logic, exception handling, timeout monitoring | HIGH | Modify task_watchdog.py API endpoints |
| **Memory System** | Silent failures | Validation, retry, backup, pooling | HIGH | Add validation layer, connection pool |
| **Performance** | Unknown metrics | Response times, task rates, GPU tracking | HIGH | Create metrics_collector.py |
| **Multi-Agent** | No routing | Task routing, handoff, health monitoring | HIGH | Add route_task method |
| **Error Recovery** | Fails without recovery | Tool fallback, checkpoints, model switching | HIGH | Add to hermes_format.py |
| **Context** | Unbounded growth | Summarization, relevance filtering | MEDIUM | Modify agent_manager.py |
| **OTA/Versioning** | Not integrated | Version registry, OTA service | HIGH | Integrate ota_upgrade.py |

---

## Next Actions

1. **Immediate (Today):**
   - Add metrics collection to main.py
   - Implement retry logic in API endpoints
   - Add memory validation layer

2. **This Week:**
   - Integrate self-planning agent with agent_manager
   - Add task checkpoint system
   - Create OTA update integration

3. **Next Sprint:**
   - Implement context summarization
   - Add multi-agent routing
   - Build monitoring dashboard endpoints

---

*This improvement plan is generated autonomously and will be updated based on test results from the LocalCrab continuous improvement cycle.*
