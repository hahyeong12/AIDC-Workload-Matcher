-- Generated snapshot: only triggers missing during read-only inspection.

-- Administrator: review the selected schema and enable stop-on-error in Workbench.

-- No accounts, passwords, grants, global settings, seed, or replacement statements.

-- Re-inspect/re-export if the database has changed. Do not execute schema.sql again.

USE `aidc_workload_matcher`;

SELECT DATABASE();

CREATE TRIGGER `aidc_workload_matcher`.`trg_job_requests_no_update`
BEFORE UPDATE ON `aidc_workload_matcher`.`job_requests` FOR EACH ROW
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Original job requests cannot be updated';

CREATE TRIGGER `aidc_workload_matcher`.`trg_job_requests_no_delete`
BEFORE DELETE ON `aidc_workload_matcher`.`job_requests` FOR EACH ROW
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Original job requests cannot be deleted';

CREATE TRIGGER `aidc_workload_matcher`.`trg_job_requirements_no_update`
BEFORE UPDATE ON `aidc_workload_matcher`.`job_requirements` FOR EACH ROW
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Original job requirements cannot be updated';

CREATE TRIGGER `aidc_workload_matcher`.`trg_job_requirements_no_delete`
BEFORE DELETE ON `aidc_workload_matcher`.`job_requirements` FOR EACH ROW
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Original job requirements cannot be deleted';
