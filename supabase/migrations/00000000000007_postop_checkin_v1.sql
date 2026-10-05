-- Post-op check-in v1: procedure-specific branching intake (decisions-and-
-- open-questions.md decision 13; replaces the flat single-checklist form).
--
-- Adds a category to each procedure so the check-in form and the red-flag
-- rules know which symptom set and sub-questions apply. Four categories:
--   abdominal_general          -- appendectomy, cholecystectomy, hernia repairs
--   abdominal_bowel_resection  -- colectomies / resections: adds bowel-
--                                  function red flags (no gas, distension)
--                                  on top of the general abdominal set
--   anorectal                  -- perianal/anal procedures: Sarah's own
--                                  fistulotomy example is the template
--   other                      -- free-text "other" procedure; generic,
--                                  conservative fallback set
--
-- Each category's actual symptom checklist and follow-up questions live in
-- apps/web's lib/symptoms.ts (form) and apps/api's core/rules.py (red-flag
-- logic), not in the database -- this column is just the routing key.

alter table procedures
  add column category text
    check (category in ('abdominal_general', 'abdominal_bowel_resection', 'anorectal', 'other'));

-- Existing rows (none in production yet; dev seed only) need a category to
-- satisfy the not-null constraint below. Default the placeholder dev-seed
-- procedures sensibly, then deactivate them in favour of Sarah's real list
-- -- deactivated rather than deleted so any check-ins already recorded
-- against them (dev/synthetic data only) keep a valid foreign key.
update procedures set category = 'abdominal_bowel_resection' where name = 'Laparoscopic colectomy';
update procedures set category = 'anorectal' where name = 'Hemorrhoidectomy';
update procedures set category = 'abdominal_bowel_resection' where name = 'Ileostomy reversal';
update procedures set active = false
  where id in (
    '00000000-0000-0000-0000-0000000000d1',
    '00000000-0000-0000-0000-0000000000d2',
    '00000000-0000-0000-0000-0000000000d3'
  );

alter table procedures alter column category set not null;

-- Sarah's real procedure list (her email, this date), tagged by category.
-- *** DRAFT PENDING HER REVIEW *** -- category assignments (which symptom
-- set and sub-questions each procedure gets) are Ian/Claude's clinical
-- inference from the procedure name, not something she specified herself.
-- See decisions-and-open-questions.md decision 13 for what's sourced from
-- her email verbatim vs. inferred.
insert into procedures (id, clinic_id, name, category)
values
  ('00000000-0000-0000-0000-0000000000f1', '00000000-0000-0000-0000-0000000000c1', 'Appendectomy', 'abdominal_general'),
  ('00000000-0000-0000-0000-0000000000f2', '00000000-0000-0000-0000-0000000000c1', 'Cholecystectomy', 'abdominal_general'),
  ('00000000-0000-0000-0000-0000000000f3', '00000000-0000-0000-0000-0000000000c1', 'Inguinal hernia repair (in the groin)', 'abdominal_general'),
  ('00000000-0000-0000-0000-0000000000f4', '00000000-0000-0000-0000-0000000000c1', 'Ventral hernia repair (on the belly)', 'abdominal_general'),
  ('00000000-0000-0000-0000-0000000000f5', '00000000-0000-0000-0000-0000000000c1', 'Right hemicolectomy', 'abdominal_bowel_resection'),
  ('00000000-0000-0000-0000-0000000000f6', '00000000-0000-0000-0000-0000000000c1', 'Sigmoid colon resection', 'abdominal_bowel_resection'),
  ('00000000-0000-0000-0000-0000000000f7', '00000000-0000-0000-0000-0000000000c1', 'Rectal cancer surgery', 'abdominal_bowel_resection'),
  ('00000000-0000-0000-0000-0000000000f8', '00000000-0000-0000-0000-0000000000c1', 'Other colon resection', 'abdominal_bowel_resection'),
  ('00000000-0000-0000-0000-0000000000f9', '00000000-0000-0000-0000-0000000000c1', 'Prolapse surgery', 'anorectal'),
  ('00000000-0000-0000-0000-0000000000fa', '00000000-0000-0000-0000-0000000000c1', 'Anal advancement flap', 'anorectal'),
  ('00000000-0000-0000-0000-0000000000fb', '00000000-0000-0000-0000-0000000000c1', 'Seton surgery for fistula', 'anorectal'),
  ('00000000-0000-0000-0000-0000000000fc', '00000000-0000-0000-0000-0000000000c1', 'Fistulotomy for fistula', 'anorectal'),
  ('00000000-0000-0000-0000-0000000000fd', '00000000-0000-0000-0000-0000000000c1', 'Hemorrhoid surgery', 'anorectal'),
  ('00000000-0000-0000-0000-0000000000fe', '00000000-0000-0000-0000-0000000000c1', 'Sphincterotomy for fissure', 'anorectal'),
  ('00000000-0000-0000-0000-0000000000ff', '00000000-0000-0000-0000-0000000000c1', 'Surgery for hidradenitis suppurativa', 'anorectal'),
  ('00000000-0000-0000-0000-000000000101', '00000000-0000-0000-0000-0000000000c1', 'Other', 'other')
on conflict (id) do nothing;
