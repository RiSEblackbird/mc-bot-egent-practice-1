package com.example.bridge.jobs;

import java.util.Collection;
import java.util.Collections;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * MiningJob を管理する軽量レジストリ。HTTP API からの操作を想定し、
 * スレッドセーフにアクセスできるよう ConcurrentHashMap を内部に持つ。
 */
public final class JobRegistry {

    private final Map<UUID, MiningJob> jobs = new ConcurrentHashMap<>();

    public MiningJob register(MiningJob job) {
        jobs.put(job.jobId(), job);
        return job;
    }

    public Optional<MiningJob> find(UUID jobId) {
        return Optional.ofNullable(jobs.get(jobId));
    }

    public void remove(UUID jobId) {
        jobs.remove(jobId);
    }

    public Collection<MiningJob> all() {
        return Collections.unmodifiableCollection(jobs.values());
    }

    public void clear() {
        jobs.clear();
    }
}
