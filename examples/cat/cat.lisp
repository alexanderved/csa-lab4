(defvar is-finished 0)

(defun cat ()
    (declare (interrupt 0))
    (let ((c (input 0)))
        (if c
            (output 1 c)
            (setq is-finished 1))))

(defun run ()
    (if is-finished
        0
        (run)))

(run)