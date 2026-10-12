from backend.services.database import db


class KYCManager:
    def __init__(self):
        pass

    def update_basic_info(self, user_id, full_name, phone, email, dob, id_number):
        query = '''UPDATE users SET full_name=%s, phone=%s, email=%s,
                   date_of_birth=%s, id_number=%s, kyc_level=GREATEST(kyc_level, 1)
                   WHERE id=%s'''
        return db._execute_query(query, (full_name, phone, email, dob, id_number, user_id), commit=True)

    def insert_document(self, user_id, doc_type, file_path):
        query = '''WITH inserted AS (
                       INSERT INTO kyc_documents (user_id, doc_type, file_path)
                       VALUES (%s, %s, %s)
                       ON CONFLICT DO NOTHING
                       RETURNING 1
                   )
                   SELECT EXISTS(SELECT 1 FROM inserted)
                       OR EXISTS(
                           SELECT 1 FROM kyc_documents
                           WHERE user_id=%s AND doc_type=%s AND file_path=%s
                       ) AS stored'''
        result = db._execute_query(
            query, (user_id, doc_type, file_path, user_id, doc_type, file_path),
            fetch=True, commit=True,
        )
        return bool(result["stored"])

    def delete_document(self, user_id, doc_type):
        query = "DELETE FROM kyc_documents WHERE user_id=%s AND doc_type=%s"
        return db._execute_query(query, (user_id, doc_type), commit=True)

    def get_documents(self, user_id):
        query = "SELECT * FROM kyc_documents WHERE user_id=%s ORDER BY uploaded_at"
        return db._execute_query(query, (user_id,), fetchall=True, commit=False)

    def update_level(self, user_id, level):
        if level == 3:
            query = "UPDATE users SET kyc_level=%s, kyc_submitted_at=NOW() WHERE id=%s"
        else:
            query = "UPDATE users SET kyc_level=%s WHERE id=%s"
        return db._execute_query(query, (level, user_id), commit=True)

    def get_user_detail(self, user_id):
        query = '''SELECT u.id, u.username, u.full_name, u.phone, u.email,
                          u.date_of_birth, u.id_number, u.kyc_level,
                          u.kyc_submitted_at, u.kyc_reviewed_at,
                          u.kyc_reviewed_by, u.kyc_reject_reason,
                          r.role_name as role
                   FROM users u LEFT JOIN roles r ON u.role_id = r.id
                   WHERE u.id=%s'''
        return db._execute_query(query, (user_id,), fetch=True, commit=False)

    def get_pending_users(self):
        query = '''SELECT u.id, u.username, u.full_name, u.phone, u.email,
                          u.id_number, u.kyc_level, u.kyc_submitted_at,
                          r.role_name as role
                   FROM users u LEFT JOIN roles r ON u.role_id = r.id
                   WHERE u.kyc_level = 3
                   ORDER BY u.kyc_submitted_at'''
        return db._execute_query(query, fetchall=True, commit=False)

    def approve(self, user_id, reviewer):
        result = db._execute_query(
            '''WITH updated_user AS (
                   UPDATE users SET kyc_level=4, kyc_reviewed_at=NOW(),
                       kyc_reviewed_by=%s, kyc_reject_reason=NULL
                   WHERE id=%s RETURNING id
               ), updated_documents AS (
                   UPDATE kyc_documents
                   SET status='approved', reviewed_at=NOW(), reviewed_by=%s
                   WHERE user_id IN (SELECT id FROM updated_user)
                   RETURNING user_id
               )
               SELECT EXISTS(SELECT 1 FROM updated_user) AS changed''',
            (reviewer, user_id, reviewer), fetch=True, commit=True,
        )
        return bool(result["changed"])

    def reject(self, user_id, reviewer, reason):
        result = db._execute_query(
            '''WITH updated_user AS (
                   UPDATE users SET kyc_level=-1, kyc_reviewed_at=NOW(),
                       kyc_reviewed_by=%s, kyc_reject_reason=%s
                   WHERE id=%s RETURNING id
               ), updated_documents AS (
                   UPDATE kyc_documents
                   SET status='rejected', reviewed_at=NOW(), reviewed_by=%s
                   WHERE user_id IN (SELECT id FROM updated_user)
                   RETURNING user_id
               )
               SELECT EXISTS(SELECT 1 FROM updated_user) AS changed''',
            (reviewer, reason, user_id, reviewer), fetch=True, commit=True,
        )
        return bool(result["changed"])

    def reset(self, user_id):
        result = db._execute_query(
            '''WITH updated_user AS (
                   UPDATE users SET kyc_level=0, kyc_submitted_at=NULL,
                       kyc_reviewed_at=NULL, kyc_reviewed_by=NULL,
                       kyc_reject_reason=NULL
                   WHERE id=%s RETURNING id
               ), deleted_documents AS (
                   DELETE FROM kyc_documents
                   WHERE user_id IN (SELECT id FROM updated_user)
                   RETURNING user_id
               )
               SELECT EXISTS(SELECT 1 FROM updated_user) AS changed''',
            (user_id,), fetch=True, commit=True,
        )
        return bool(result["changed"])

