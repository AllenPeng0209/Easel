import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

test('a quick switch to video never mounts the unused chat surface; returning to a loaded chat is immediate', () => {
  let value=false, effect, cleanup, clock=0, nextId=0;
  const timers=new Map();
  const { useDeferredMount }=componentModule('lib/useDeferredMount.ts',{
    externals:{react:{useState:()=>[value,next=>{value=next;}],useEffect:next=>{effect=next;}}},
    globals:{setTimeout:(fn,delay)=>{const id=++nextId;timers.set(id,{fn,at:clock+delay});return id;},clearTimeout:id=>timers.delete(id)},
  });
  const render=active=>{cleanup?.();const result=useDeferredMount(active);cleanup=effect();return result;};
  const advance=ms=>{clock+=ms;for(const [id,timer] of timers)if(timer.at<=clock){timers.delete(id);timer.fn();}};
  assert.equal(render(true),false);
  advance(100);
  assert.equal(render(false),false);
  advance(2000);
  assert.equal(value,false,'Leaving for video must cancel the pending chat module mount');
  assert.equal(timers.size,0);
  assert.equal(render(true),false);
  advance(1000);
  assert.equal(render(true),true,'A user who stays in chat must get the complete surface');
  assert.equal(render(false),false);
  assert.equal(render(true),true,'A previously loaded chat should not wait again');
  assert.equal(timers.size,0);
});
