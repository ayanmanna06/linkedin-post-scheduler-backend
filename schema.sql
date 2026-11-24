-- =============================================================================
-- LinkedIn Post Scheduler — MySQL 8.0 DDL Schema
-- Character Set: utf8mb4, Collation: utf8mb4_unicode_ci
-- =============================================================================
CREATE DATABASE IF NOT EXISTS `linkedin_scheduler`;
USE `linkedin_scheduler`;
CREATE TABLE IF NOT EXISTS `users` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `linkedin_sub` VARCHAR(128) NOT NULL,
    `display_name` VARCHAR(255) NOT NULL,
    `email` VARCHAR(255) NULL,
    `password_hash` VARCHAR(255) NULL,
    `avatar_url` VARCHAR(1024) NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT `uq_users_linkedin_sub` UNIQUE (`linkedin_sub`),
    INDEX `ix_users_linkedin_sub` (`linkedin_sub`)
);

CREATE TABLE IF NOT EXISTS `linkedin_connections` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `user_id` INT NOT NULL,
    `author_urn` VARCHAR(255) NOT NULL,
    `access_token_encrypted` TEXT NOT NULL,
    `refresh_token_encrypted` TEXT NULL,
    `expires_at` DATETIME NOT NULL,
    `scopes` VARCHAR(512) NOT NULL DEFAULT 'openid profile w_member_social',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT `uq_linkedin_connections_user_id` UNIQUE (`user_id`),
    CONSTRAINT `fk_linkedin_connections_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
    INDEX `ix_linkedin_connections_user_id` (`user_id`)
);

CREATE TABLE IF NOT EXISTS `posts` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `user_id` INT NOT NULL,
    `content` TEXT NOT NULL,
    `source` ENUM('manual', 'ai') NOT NULL DEFAULT 'manual',
    `ai_brief` JSON NULL,
    `status` ENUM('scheduled', 'processing', 'published', 'failed', 'cancelled') NOT NULL DEFAULT 'scheduled',
    `scheduled_at_utc` DATETIME NOT NULL,
    `next_attempt_at` DATETIME NOT NULL,
    `attempt_count` INT NOT NULL DEFAULT 0,
    `idempotency_key` VARCHAR(64) NOT NULL,
    `linkedin_post_urn` VARCHAR(255) NULL,
    `last_error` TEXT NULL,
    `locked_at` DATETIME NULL,
    `locked_by` VARCHAR(128) NULL,
    `published_at` DATETIME NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT `uq_posts_idempotency_key` UNIQUE (`idempotency_key`),
    CONSTRAINT `fk_posts_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
    INDEX `ix_posts_idempotency_key` (`idempotency_key`),
    INDEX `ix_posts_status_next_attempt_at` (`status`, `next_attempt_at`),
    INDEX `ix_posts_user_id_status` (`user_id`, `status`),
    INDEX `ix_posts_user_id` (`user_id`)
);

CREATE TABLE IF NOT EXISTS `post_attempts` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `post_id` INT NOT NULL,
    `attempt_no` INT NOT NULL,
    `outcome` ENUM('success', 'transient_failure', 'fatal_failure', 'uncertain') NOT NULL,
    `http_status` INT NULL,
    `error_message` TEXT NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT `fk_post_attempts_post_id` FOREIGN KEY (`post_id`) REFERENCES `posts` (`id`) ON DELETE CASCADE,
    INDEX `ix_post_attempts_post_id` (`post_id`)
);